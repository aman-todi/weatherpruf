# Deployment

The whole product ships as **one container image** running as an **ECS Express
Mode** service in **`us-east-2`**: it serves the React bundle at `/`, the REST
API at `/api`, and the MCP connector at `/mcp` — one origin, one TLS
certificate, one deploy. Express Mode provisions the Fargate service, the load
balancer, the certificate, auto-scaling and a public HTTPS URL from just an
image plus two IAM roles, so there is no task or service definition checked in
here. After the one-time setup below, `.github/workflows/deploy.yml` handles
every deploy.

**v1 is live** at `https://app.weatherpruf.live` (the connector URL is
`https://app.weatherpruf.live/mcp/` — **the trailing slash matters**). The
Express-Mode-generated `https://we-e6c4de001ecb4a2d83d6b9b625171338.ecs.us-east-2.on.aws`
still resolves to the same service. This account is on the **new AWS experience**
with a managed-IAM project, which shapes several of the decisions below — read
the "Redos" section at the end before assuming this matches a vanilla AWS
account.

## Architecture & design decisions

**One origin, one container.** Serving the frontend from the same container as
the API is what removes CORS, a second certificate, a second domain and a second
deploy pipeline. `backend/app/static.py` serves the built bundle and falls back
to `index.html` for any path that is not a real file and not an application
route, so client-side routes like `/oauth/consent` resolve while `/api/*` still
returns JSON. At single-digit concurrent users a CDN in front of the static
assets would buy nothing worth that complexity.

**ECS Express Mode over a hand-rolled service.** Express Mode owns the ALB,
target groups, security groups, TLS certificate, auto-scaling and the public
`*.ecs.<region>.on.aws` URL. We supply an image, a container port, a health
path, CPU/memory, a task-count range and two IAM roles; ECS creates and manages
the rest. That is why there is no task-definition or service YAML to maintain.

**Secrets as JSON keys, injected at task start.** Everything sensitive lives in
a single Secrets Manager secret, referenced key-by-key from the task definition
(`<arn>:KEY::`), so no secret value ever lands in the task definition, the ECS
console, or a workflow log. ECS reads secrets **at task start only** — rotating a
secret does not reach running tasks, so force a new deployment after rotating.

**The read-only database role is a real security boundary.** `DATABASE_URL` and
`READONLY_DATABASE_URL` are deliberately different credentials.
`query_closet_items` (spec §4.1) runs on the read-only role; the sqlglot AST
validation in front of it is defence in depth, not the boundary. Giving the
query path the application role's credentials would quietly remove the only real
guarantee, and nothing would visibly break to tell you.

**Database reached through the Supabase Session pooler (IPv4).** Supabase's
direct connection (`db.<ref>.supabase.co:5432`) is **IPv6-only** without the
paid IPv4 add-on, and the default VPC that Express Mode places tasks in is
**IPv4-only**. So both connection strings use the Session pooler
(`aws-0-<region>.pooler.supabase.com:5432`, IPv4), and the usernames carry the
project ref: `postgres.<project-ref>` and `wardrobe_readonly.<project-ref>`.
Session mode (port 5432, not the transaction pooler's 6543) preserves prepared
statements and per-transaction `SET`s, which is what `app/db.py` relies on.

**CI authenticates with a scoped IAM access-key user, not GitHub OIDC.** The
managed-IAM project denies `iam:*Provider*` at every plan level, so a GitHub
OIDC identity provider cannot be created here and OIDC federation is impossible.
Instead a locked-down IAM user (`weatherpruf-ci`) whose only permission is to
assume the deploy role holds the access keys; all deploy permissions stay on the
role. See Redos #2 for the security trade-off and mitigations.

**`SUPABASE_ANON_KEY` is public on purpose.** Vite inlines it into the bundle at
build time, so it is public the moment anyone loads the page — that is why it is
a repository **variable**, not a secret. RLS is what protects the data. The
`service_role` key is the opposite and lives only in Secrets Manager.

## One-time setup

Region is `us-east-2` throughout. `<account>` is the project's AWS account ID.

### 1. ECR repository

```bash
aws ecr create-repository --repository-name weatherpruf-backend --region us-east-2
aws ecr put-lifecycle-policy --repository-name weatherpruf-backend --region us-east-2 \
  --lifecycle-policy-text '{
    "rules": [{
      "rulePriority": 1,
      "description": "Expire untagged images after 7 days",
      "selection": {"tagStatus": "untagged", "countType": "sinceImagePushed",
                    "countUnit": "days", "countNumber": 7},
      "action": {"type": "expire"}
    }]
  }'
```

Leave tags **mutable**: the workflow tags images by commit SHA, and a
`workflow_dispatch` re-run of the same commit re-pushes the same tag — immutable
tags would reject that.

### 2. Service-linked roles (first ECS use in the account)

ECS Express Mode assumes several service-linked roles. A brand-new account has
none, and the first `create-express-gateway-service` fails with *"Unable to
assume the service linked role"* until they exist. Create them once:

```bash
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com
aws iam create-service-linked-role --aws-service-name elasticloadbalancing.amazonaws.com
aws iam create-service-linked-role --aws-service-name ecs.application-autoscaling.amazonaws.com
```

They are eventually consistent — wait ~1 minute before the first deploy.

### 3. IAM roles

**Task execution role** `weatherpruf-task-execution` — lets ECS pull the image,
write logs, and read the one secret. Trust `ecs-tasks.amazonaws.com`, attach the
AWS-managed `AmazonECSTaskExecutionRolePolicy`, plus this inline policy scoped to
the secret alone:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:us-east-2:<account>:secret:weatherpruf/backend-*"
  }]
}
```

The trailing `-*` is required: Secrets Manager appends a random six-character
suffix, so an exact ARN stops matching after a delete-and-recreate.

**Infrastructure role** `weatherpruf-infrastructure` — lets Express Mode manage
the load balancer, target groups, security groups, certificate and scaling.
Trust `ecs.amazonaws.com` and attach the AWS-managed policy by ARN (referencing
the managed policy, so it tracks AWS's updates rather than drifting like a
hand-copied one):

```bash
aws iam attach-role-policy --role-name weatherpruf-infrastructure \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
```

**Deploy role** `weatherpruf-github-deploy` — what the CI workflow assumes.
Trust the CI user (step 4) for both `sts:AssumeRole` **and `sts:TagSession`**
(`aws-actions/configure-aws-credentials` tags the assumed session; without
`TagSession` the assume fails):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::<account>:user/weatherpruf-ci"},
    "Action": ["sts:AssumeRole", "sts:TagSession"]
  }]
}
```

Permissions policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "arn:aws:ecr:us-east-2:<account>:repository/weatherpruf-backend"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecs:CreateExpressGatewayService", "ecs:UpdateExpressGatewayService",
        "ecs:DescribeExpressGatewayService", "ecs:RegisterTaskDefinition",
        "ecs:DescribeClusters", "ecs:DescribeServices", "ecs:CreateCluster",
        "ecs:ListServiceDeployments", "ecs:DescribeServiceDeployments",
        "ecs:TagResource", "ecs:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::<account>:role/weatherpruf-task-execution",
        "arn:aws:iam::<account>:role/weatherpruf-infrastructure"
      ]
    }
  ]
}
```

`iam:PassRole` is scoped to exactly the two roles this deploy may hand to ECS, so
a compromised workflow cannot pass a more privileged role into a task. The
`ecs:*` actions take `"*"` because the service ARN does not exist until the first
create; tighten to the service ARN after the first deploy if you want.

### 4. CI user and access keys

```bash
aws iam create-user --user-name weatherpruf-ci
aws iam put-user-policy --user-name weatherpruf-ci \
  --policy-name assume-weatherpruf-deploy-role \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::<account>:role/weatherpruf-github-deploy"
    }]
  }'
aws iam create-access-key --user-name weatherpruf-ci
```

The user can do nothing but assume the deploy role; all real permissions live on
the role. Store the returned key/secret as the GitHub secrets in step 6 and do
not keep another copy. Rotate periodically (`create-access-key`, update the two
GitHub secrets, then `delete-access-key` the old one).

### 5. Secrets Manager secret

One secret, `weatherpruf/backend`, with exactly these five JSON keys:

```jsonc
{
  "DATABASE_URL":            "postgresql://postgres.<project-ref>:<url-encoded-pw>@aws-0-<region>.pooler.supabase.com:5432/postgres",
  "READONLY_DATABASE_URL":   "postgresql://wardrobe_readonly.<project-ref>:<url-encoded-pw>@aws-0-<region>.pooler.supabase.com:5432/postgres",
  "SUPABASE_SERVICE_ROLE_KEY": "<service_role key>",
  "SUPABASE_JWT_SECRET":     "",
  "MCP_OAUTH_JWT_SIGNING_KEY": "<random 48-byte hex>"
}
```

- Both DB URLs use the **Session pooler** host and dotted usernames (see the
  architecture note). **Percent-encode the passwords** — the read-only password
  contained URL-special characters that made asyncpg misparse the DSN (Redos #6).
- `SUPABASE_JWT_SECRET` is **empty** because the project uses asymmetric JWT
  keys, but the key must still be **present**: the workflow references
  `<arn>:SUPABASE_JWT_SECRET::`, and ECS fails to start the task if a referenced
  JSON key is missing. Empty is fine; absent is not.
- `MCP_OAUTH_JWT_SIGNING_KEY` signs the reference tokens the MCP OAuth proxy
  issues to connectors (see "MCP connector OAuth" below). Generate a stable
  random value (`python -c "import secrets; print(secrets.token_hex(48))"`);
  rotating it invalidates every live connector token, so clients re-authorize.
- `DATABASE_URL` and `READONLY_DATABASE_URL` must be different credentials.

### 6. Repository configuration (`aman-todi/weatherpruf`)

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | The CI user's access key ID. |
| `AWS_SECRET_ACCESS_KEY` | The CI user's secret access key. |
| `AWS_DEPLOY_ROLE_ARN` | The deploy role the CI user assumes. |
| `AWS_TASK_EXECUTION_ROLE_ARN` | The task execution role. |
| `AWS_INFRASTRUCTURE_ROLE_ARN` | The infrastructure role. |
| `AWS_APP_SECRET_ARN` | ARN of the Secrets Manager secret above. |

**Variables** (same page → Variables) — non-sensitive, so they stay readable:

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | the anon key — read at **build** time, inlined into the bundle |
| `SUPABASE_OAUTH_CLIENT_ID` | the upstream Supabase OAuth client the MCP proxy uses (a public client id — see below) |
| `PUBLIC_BASE_URL` | the public origin — `https://app.weatherpruf.live` (the `*.ecs.us-east-2.on.aws` URL still works too) |
| `DAILY_MCP_CALL_LIMIT` | `50` |

There is no `CORS_ALLOW_ORIGINS` and no `VITE_API_BASE_URL`: the frontend is
served from the same origin as the API, so the client uses relative URLs and no
cross-origin request is ever made.

## MCP connector OAuth (the app fronts Supabase's OAuth server)

Claude.ai's remote connector expects the **MCP server itself** to be the OAuth
authorization server: it does Dynamic Client Registration and runs the whole
flow against *this* origin (`POST /register`, `/authorize`, `/token`,
`/.well-known/oauth-authorization-server`). It does **not** follow the RFC 9728
`authorization_servers` pointer to an external server. Supabase is the real
authorization server, so the app **bridges** the two with a FastMCP `OAuthProxy`
(`app/mcp_server/auth.py`): it serves those OAuth endpoints at the app origin and
proxies them to Supabase. `app/main.py` re-exposes the proxy's routes at the root
because FastMCP registers them on the `/mcp` sub-app.

The token model: a connector holds a FastMCP-issued **reference** token; on each
`/mcp` request the proxy swaps it for the stored upstream Supabase token and
re-validates it through the same `SupabaseTokenVerifier` the REST path uses — so
identity and RLS are unchanged.

Two pieces of setup this needs:

1. **A public upstream client registered with Supabase**, whose `redirect_uri`
   is `{PUBLIC_BASE_URL}/auth/callback` (i.e. `https://app.weatherpruf.live/auth/callback`).
   Register it once via Supabase's DCR endpoint and put its (public) client id in
   the `SUPABASE_OAUTH_CLIENT_ID` variable:

   ```bash
   curl -sS -X POST https://<project-ref>.supabase.co/auth/v1/oauth/clients/register \
     -H 'content-type: application/json' -d '{
       "client_name": "weatherpruf MCP proxy",
       "redirect_uris": ["https://app.weatherpruf.live/auth/callback"],
       "grant_types": ["authorization_code","refresh_token"],
       "response_types": ["code"],
       "token_endpoint_auth_method": "none"
     }'
   ```

2. **`MCP_OAUTH_JWT_SIGNING_KEY`** in the secret (above) — `OAuthProxy` requires
   a stable signing key when the upstream client is public (has no secret).

When both `SUPABASE_OAUTH_CLIENT_ID` and `MCP_OAUTH_JWT_SIGNING_KEY` are set the
proxy is active; otherwise the app falls back to advertising Supabase as an
external authorization server (correct per spec, but Claude's connector can't use
it). Supabase side: enable the **OAuth 2.1 server** and **Allow Dynamic OAuth
Apps** (DCR), set the **Authorization Path** to `/oauth/consent`, and add
`https://app.weatherpruf.live` as a **Site URL** plus `https://app.weatherpruf.live/**`
under **Redirect URLs** (the `/**` matters — the post-login redirect carries the
`authorization_id` on a sub-path). See `docs/setup-supabase.md` §5.

**Single task.** The proxy's upstream-token store is per-instance and in-memory,
so the service is pinned to **one** Fargate task (`max-task-count: 1` in the
workflow). Consequences: connected users must re-authorize once after each deploy
(a task restart empties the store), and there is no horizontal scaling. The
upgrade path is a shared/persistent store (e.g. a Postgres-backed `AsyncKeyValue`)
which would let `max-task-count` rise again.

To connect: in Claude.ai, add a custom connector at `https://app.weatherpruf.live/mcp/`
(trailing slash), choose **Sign in now** + **Register automatically**.

## Deploying

Pushes to `main` that touch `backend/`, `frontend/`, `infra/Dockerfile` or the
workflow run the pipeline: it applies migrations to a throwaway Postgres, runs
lint, builds the frontend and runs the test suite, and only then builds, pushes
and deploys. You can also run it by hand from the Actions tab
(`workflow_dispatch`).

Migrations against the **real** Supabase project are **not** part of this
pipeline — apply those yourself, before the deploy that depends on them.

**Impact on users.** Express Mode does a rolling deployment behind the ALB: it
starts the new task, waits for it to pass `/health`, registers it, then drains
the old one. No downtime for the web app, and web-app auth is a browser-held
Supabase JWT so nobody is signed out there. Two caveats: the frontend bundle is
cached in already-open tabs, so users must reload to pick up a new build; and the
**MCP connector must be re-authorized after each deploy**, because the OAuth
proxy's in-memory token store is emptied when the single task restarts (see "MCP
connector OAuth"). A failed deploy over a healthy service rolls back and keeps the
old task serving. **Re-check the ALB host-header rule after each deploy** — see
"Custom domain and TLS".

## `PUBLIC_BASE_URL` — the first-deploy chicken-and-egg

`PUBLIC_BASE_URL` is what the "Connect your assistant" page shows the user (with
`/mcp` appended), but Express Mode only generates the URL on the first deploy. So
the first deploy uses a placeholder; then read the URL back, set the variable,
and redeploy. **Expect two deploys** the first time. After that it is stable.

```bash
aws ecs describe-express-gateway-service \
  --service-arn arn:aws:ecs:us-east-2:<account>:service/default/weatherpruf-backend \
  --region us-east-2 --output json | grep -o '[a-z0-9-]*\.ecs\.us-east-2\.on\.aws'
```

Then set `PUBLIC_BASE_URL` to `https://<that host>`, add it as a Supabase **Site
URL** and **Redirect URL** (keep `http://localhost:5173` too, for local magic
links), and redeploy.

## Logs and retention

Express Mode creates the log group (`/aws/ecs/default/weatherpruf-backend-*`) and
by default it never expires. Set a retention period so it does not bill forever:

```bash
aws logs put-retention-policy \
  --log-group-name /aws/ecs/default/weatherpruf-backend-<suffix> --retention-in-days 30
```

Do not log at DEBUG in production. `app/auth.py` logs rejected tokens at INFO
with the reason but never the token itself; keep it that way.

## Checking a deploy

```bash
curl -s https://<service-url>/health
# {"status":"ok","environment":"production","database":{"app":"ok","readonly":"ok"}}
```

A `readonly` status of `not_configured` means `READONLY_DATABASE_URL` did not
reach the container. An unauthenticated `GET /api/categories` should return a
JSON `401` (proof the frontend can reach its own API), and `/api/<nonsense>`
should return a JSON `404`, not the SPA shell.

## Custom domain and TLS — `app.weatherpruf.live` (as built)

Express Mode's generated URL is already HTTPS with a managed certificate, so a
custom domain is polish, not a prerequisite. v1 uses `app.weatherpruf.live`,
added **on the existing Express Mode ALB** (not a separate distribution) so both
hostnames serve the same service. What was done, all in `us-east-2`:

1. **ACM certificate** for `app.weatherpruf.live`, DNS-validated and
   auto-renewing:
   `arn:aws:acm:us-east-2:038223566275:certificate/132d6a8b-cc81-4df3-8eff-68c37cd581c5`.
   Its validation record **must stay in DNS forever** or auto-renewal breaks:
   `_fd94470a1c8e3ec497d23bd7e4f2e8a2.app.weatherpruf.live` CNAME
   `_d4479f9cff01885681a349d79ad4fd73.wzccmgtwzk.acm-validations.aws`.
2. **DNS** (Squarespace, not Route 53): `app` CNAME →
   `ecs-express-gateway-alb-1feb04cc-1253170358.us-east-2.elb.amazonaws.com`.
3. **443 listener** — the cert added alongside the ECS-managed `…on.aws` cert
   (SNI picks the right one).
4. **Host-header rule** — forwards **both** `…on.aws` and `app.weatherpruf.live`
   to the target group. Keep both values so the generated URL keeps working.
5. **New `:80` listener** — `301`-redirects to HTTPS.
6. Then `PUBLIC_BASE_URL` → `https://app.weatherpruf.live` and the Supabase
   Site/Redirect URLs updated (see "MCP connector OAuth"), and redeploy.

Key ARNs: ALB `…:loadbalancer/app/ecs-express-gateway-alb-1feb04cc/0a0145f04d6332b8`;
443 listener `…/a01db0fb6e5579a0`; ALB SG `sg-0bec86e4820a0b43e`; task SG
`sg-0433edf7e1df78098`.

> **Drift after a deploy.** `UpdateExpressGatewayService` can rewrite the
> host-header rule and drop `app.weatherpruf.live`. Re-check (and re-apply if
> needed) after every deploy — the rule ARN can change, so discover it
> dynamically:
>
> ```bash
> export AWS_PROFILE=agent-toolkit AWS_REGION=us-east-2
> LISTENER=arn:aws:elasticloadbalancing:us-east-2:038223566275:listener/app/ecs-express-gateway-alb-1feb04cc/0a0145f04d6332b8/a01db0fb6e5579a0
> RULE=$(aws elbv2 describe-rules --listener-arn "$LISTENER" \
>   --query "Rules[?Actions[0].Type=='forward'].RuleArn | [0]" --output text)
> aws elbv2 modify-rule --rule-arn "$RULE" \
>   --conditions Field=host-header,HostHeaderConfig="{Values=[we-e6c4de001ecb4a2d83d6b9b625171338.ecs.us-east-2.on.aws,app.weatherpruf.live]}"
> ```
>
> The cert on the 443 listener and the `:80` redirect listener are additive and
> survive; the host-header rule is the one to watch.

## Rotating credentials

- **Read-only role password** — `alter role wardrobe_readonly with login
  password '<new>'` in Supabase, rebuild `READONLY_DATABASE_URL`
  (**percent-encoded**), update the secret, force a new deployment.
- **`service_role` key** — rotating it in the dashboard invalidates the old key
  immediately, so update the secret and redeploy in the same window.
- **CI access key** — `create-access-key`, update the two GitHub secrets,
  `delete-access-key` the old one.
- **JWT signing keys** — Supabase rotates with an overlap; the backend caches
  JWKS for ten minutes, so allow that before the new key is picked up.

## Building locally

```bash
docker build -f infra/Dockerfile -t weatherpruf \
  --build-arg VITE_SUPABASE_URL="https://<project-ref>.supabase.co" \
  --build-arg VITE_SUPABASE_ANON_KEY="<anon key>" \
  .
docker run --rm -p 8000:8000 --env-file backend/.env weatherpruf
```

The whole app is then at `http://localhost:8000`. Omitting the build args
produces a working image whose frontend cannot reach Supabase and shows its setup
screen naming the missing values.

---

## Redos — what the first real setup changed, and why

This runbook was originally written without access to the live AWS docs, against
the assumptions of a vanilla AWS account. Standing the project up on the new AWS
experience surfaced the following; each is now folded into the steps above.

1. **Region `us-east-1` → `us-east-2`.** The project's single assigned Region is
   `us-east-2`; the managed SCP denies Regional resources anywhere else. Every
   ARN, the Secrets Manager and ECR region, and the workflow's `AWS_REGION` moved
   accordingly.

2. **GitHub OIDC → IAM access-key user.** The managed-IAM SCP
   (`DenyIAMRestrictedActions`) denies `iam:*Provider*` — creating or even listing
   an OIDC identity provider — and the deny is present in **both** the Free and
   Paid plan policies, documented "cannot be modified." OIDC federation is
   therefore impossible here. `sts:AssumeRoleWithWebIdentity` failed with "token
   could not be validated." Replacement: a scoped IAM user (`weatherpruf-ci`) that
   may only assume the deploy role, with its access keys in GitHub secrets. The
   trade-off is a long-lived key (what OIDC would have avoided); mitigations are
   tight scoping (assume-role only), permissions kept on the role, and rotation.

3. **`sts:TagSession` on the deploy role trust.** `configure-aws-credentials`
   tags the assumed session, so the role's trust policy must allow `TagSession`
   in addition to `AssumeRole`, or the assume fails.

4. **Service-linked roles created (step 2).** The account had never run ECS, so
   `AWSServiceRoleForECS` (and the ELB / Application Auto Scaling SLRs) did not
   exist; the first create failed with "Unable to assume the service linked
   role."

5. **Direct DB connection → Session pooler.** The default VPC is IPv4-only and
   the Supabase direct host is IPv6-only, so tasks could not reach the database.
   Both connection strings moved to the Session pooler (IPv4) with
   `postgres.<ref>` / `wardrobe_readonly.<ref>` usernames.

6. **Percent-encode the DB passwords.** The read-only password contained
   URL-special characters; unencoded, asyncpg parsed part of the password as the
   port and the container crash-looped at startup. Encoding the password in the
   DSN fixed it. **Note:** the crash traceback printed the password into
   CloudWatch before the fix — rotate the read-only password.

7. **`ecs:UntagResource` added to the deploy policy.** The official
   `amazon-ecs-deploy-express-service` action lists it; without it, tag mutation
   on update can fail.

8. **ECR tags kept mutable.** An earlier attempt set them immutable, which would
   reject a `workflow_dispatch` re-run of the same commit SHA (same tag).

9. **Frontend `new URL()` needed a base.** In the single-origin deployment
   `VITE_API_BASE_URL` is unset, so `frontend/src/lib/api.ts` built a relative
   URL string; `new URL('/api/...')` with no base throws, which surfaced to users
   as "Could not reach the server at ." — the SPA never called its own API.
   Fixed by passing `window.location.origin` as the base (resolves the relative
   case, ignored when a base URL is set for a future split origin).

10. **`SUPABASE_JWT_SECRET` present-but-empty.** The project uses asymmetric JWT
    keys, so the value is empty, but the key must exist in the secret JSON or ECS
    refuses to start the task on the missing `<arn>:SUPABASE_JWT_SECRET::`
    reference.

11. **The MCP connector needs same-origin OAuth (OAuth proxy).** The original
    design used FastMCP's `RemoteAuthProvider`, which only *advertises* Supabase
    as the authorization server via RFC 9728. Claude's connector ignores that
    pointer and does DCR + the whole OAuth flow against the MCP server's own
    origin — it `POST`ed `/register` to the app (404/405) and could not register.
    The app now runs a FastMCP `OAuthProxy` that serves `/authorize`, `/token`,
    `/register` and the authorization-server metadata at this origin and bridges
    them to Supabase (see "MCP connector OAuth"). This added the
    `SUPABASE_OAUTH_CLIENT_ID` variable, the `MCP_OAUTH_JWT_SIGNING_KEY` secret,
    a Supabase upstream client, and the single-task pin. (An interim fix — 404ing
    unknown `/.well-known/` paths so the SPA fallback stops answering discovery
    probes with HTML — is kept: it is correct regardless.)

12. **Custom domain on the existing ALB, via Squarespace, not Route 53.** DNS is
    on Squarespace, so the domain is a CNAME to the ALB rather than a Route 53
    alias, and the cert/listener/host-rule were added to the Express Mode ALB in
    place. The host-header rule is subject to drift on redeploys — see "Custom
    domain and TLS".
