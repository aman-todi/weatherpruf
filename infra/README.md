# Deployment

One container image serves both the REST API and the MCP sub-app, running as an
**ECS Express Mode** service. Express Mode provisions the Fargate service, the
load balancer, a TLS certificate, auto-scaling and a public HTTPS URL from just
an image plus two IAM roles — so there is no task definition or service
definition checked in here. The one-time setup below is what you do by hand;
after that, `.github/workflows/deploy.yml` handles every deploy.

> **Verified how far?**
>
> `docker build` has never been run against this Dockerfile: Docker Hub's blob
> CDN is blocked by the development environment's network egress policy, so no
> base image can be pulled there. What *was* verified is the part that actually
> tends to break — the build's install step, reproduced outside Docker in a
> clean virtualenv from nothing but `backend/pyproject.toml` and `backend/app`.
> It installs, and `app.main` then imports from `site-packages` (not from a
> stray source copy) with all eight routes registered. So the layer that
> remains unproven is the base image, `apt-get`, and the `USER`/`HEALTHCHECK`
> plumbing — not the application build. Run it once before trusting it.
>
> The AWS steps are likewise second-hand: `docs.aws.amazon.com` is blocked from
> that environment too, so the Express Mode specifics here follow the official
> [`aws-actions/amazon-ecs-deploy-express-service`](https://github.com/aws-actions/amazon-ecs-deploy-express-service)
> action's documented inputs rather than a reading of the AWS docs. Check the
> current docs before the first deploy.

## One-time setup

### 1. ECR repository

```bash
aws ecr create-repository --repository-name weatherpruf-backend --region us-east-1
```

### 2. IAM roles

Express Mode needs two roles, plus one for GitHub Actions to assume:

| Role | Purpose |
|---|---|
| **Task execution role** | Lets ECS pull the image from ECR, write logs to CloudWatch, and read the Secrets Manager secret below. Start from the AWS-managed `AmazonECSTaskExecutionRolePolicy` and add `secretsmanager:GetSecretValue` on your secret's ARN. |
| **Infrastructure role** | Lets Express Mode create and manage the load balancer, target groups, security groups and scaling policies on your behalf. |
| **GitHub Actions deploy role** | Trusts GitHub's OIDC provider for this repository. Needs ECR push, `ecs:CreateExpressGatewayService` / `ecs:UpdateExpressGatewayService` / `ecs:DescribeExpressGatewayService`, `ecs:RegisterTaskDefinition`, and `iam:PassRole` for the two roles above. |

Use OIDC for the deploy role rather than storing long-lived AWS keys as
repository secrets.

### 3. Secrets

Everything sensitive lives in a single Secrets Manager secret, read as
individual JSON keys so no secret value ever lands in the task definition, the
ECS console, or a workflow log:

```bash
aws secretsmanager create-secret \
  --name weatherpruf/backend \
  --secret-string '{
    "DATABASE_URL": "postgresql://...",
    "READONLY_DATABASE_URL": "postgresql://wardrobe_readonly:...",
    "SUPABASE_SERVICE_ROLE_KEY": "...",
    "SUPABASE_JWT_SECRET": "..."
  }'
```

`DATABASE_URL` and `READONLY_DATABASE_URL` **must** be different credentials.
The read-only role is the actual security boundary behind `query_closet_items`
(spec §4.1) — the AST validation in front of it is defence in depth. Giving the
query path the application role's credentials would quietly remove the only
real guarantee.

Note that rotating this secret does not reach running tasks: ECS injects
secrets at task start, so force a new deployment afterwards.

### 4. Repository configuration

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Name | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | The GitHub Actions deploy role. |
| `AWS_TASK_EXECUTION_ROLE_ARN` | The task execution role. |
| `AWS_INFRASTRUCTURE_ROLE_ARN` | The infrastructure role. |
| `AWS_APP_SECRET_ARN` | ARN of the Secrets Manager secret above. |

**Variables** (same page → Variables) — non-sensitive, so they stay readable:

| Name | Example |
|---|---|
| `SUPABASE_URL` | `https://abcdefgh.supabase.co` |
| `PUBLIC_BASE_URL` | `https://wardrobe.example.com` |
| `CORS_ALLOW_ORIGINS` | `https://wardrobe.example.com` |
| `DAILY_MCP_CALL_LIMIT` | `10` |

`PUBLIC_BASE_URL` is what the web app's "Connect your assistant" page shows the
user, with `/mcp` appended. Set it to the URL people will actually paste into
Claude.ai — which is a chicken-and-egg on the very first deploy, since Express
Mode generates the URL. Deploy once, read the URL back, set the variable, and
redeploy.

## Custom domain and TLS

Claude.ai's remote connector requires HTTPS. **Express Mode already satisfies
that**: the URL it generates is HTTPS with a managed certificate, so a custom
domain is polish rather than a prerequisite, and it is entirely reasonable to
ship v1 on the generated URL.

For a custom domain: request a certificate for it in ACM in the same region,
attach it to the service's load balancer, and point a Route 53 alias record at
that load balancer. Then update `PUBLIC_BASE_URL` and `CORS_ALLOW_ORIGINS` and
redeploy so the Connect page hands out the new URL.

## Deploying

Pushes to `main` that touch `backend/` or the Dockerfile run the workflow: it
applies the migrations to a throwaway Postgres and runs lint plus the full test
suite, and only then builds, pushes and deploys. Database migrations against
the real Supabase project are **not** part of this pipeline — apply those
yourself, before the deploy that depends on them.

You can also run it by hand from the Actions tab (`workflow_dispatch`).

## Checking a deploy

```bash
curl -s https://<service-url>/health
# {"status":"ok","environment":"production","database":{"app":"ok","readonly":"ok"}}
```

A `readonly` status of `not_configured` means `READONLY_DATABASE_URL` did not
reach the container: the REST API will work and `query_closet_items` will not.

## Building locally

```bash
docker build -f infra/Dockerfile -t weatherpruf .
docker run --rm -p 8000:8000 --env-file backend/.env weatherpruf
```

On Linux, a `DATABASE_URL` pointing at `127.0.0.1` refers to the container, not
your host — use `--network host`, or point it at your host's LAN address.
