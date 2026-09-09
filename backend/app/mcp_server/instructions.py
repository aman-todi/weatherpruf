"""The instructional text the assistant actually reads.

Spec §1 is explicit that this is not decoration. Skills do not reach plain
Claude.ai chat and MCP prompts are user-invoked rather than consulted while the
model decides how to call a tool. What *does* steer autonomous tool use on
every client is two things, and this module holds both of them:

* rich **tool descriptions**, read when the model is deciding whether and how
  to call a tool — see ``BATCH_ADD_ITEMS_DESCRIPTION``, drafted in spec §4.2 as
  system-prompt-style instructions;
* instructional content returned **inside a tool's own response** — see
  ``QUERY_INSTRUCTIONS``, handed back by ``get_closet_structure`` so the
  assistant is given the query grammar up front instead of guessing at it.

They are kept here rather than inline in the tool bodies so the wording can be
read and revised as prose.
"""

from __future__ import annotations

SERVER_INSTRUCTIONS = """\
This server is a user's digital closet. You add clothes to it, read what is in \
it, and filter it.

Two things it deliberately does NOT do, which are yours to handle:

1. It has no weather tool and will never return weather. When an outfit \
question depends on conditions, find the weather yourself — from what you \
already know, from web search if you have it, or by asking the user. \
`get_user_profile` gives you their home location and whether they think in \
Fahrenheit or Celsius, so you usually do not need to ask again.
2. It does no outfit reasoning. It returns items; choosing between them, and \
explaining the choice, is your job.

Call `get_closet_structure` once at the start of a conversation that will touch \
the closet. It returns every category, the fields each category supports, and \
the exact grammar for `query_closet_items` — with it you will not have to guess \
at a category id or a query operator.

Calls are capped per user per day and the budget is tight, so prefer one call \
that answers the question over several that circle it: `batch_add_items` rather \
than repeated `add_item`, `get_closet_summary` rather than several exploratory \
queries, and `list_category_items` rather than a `query_closet_items` predicate \
that only filters on category."""


QUERY_INSTRUCTIONS = """\
HOW TO BUILD A where_clause FOR query_closet_items

Pass a single SQL boolean expression — a WHERE-clause predicate on its own, with
no SELECT, no FROM, no semicolon, and no trailing comment. The server adds the
user scoping, the row limit and everything else. Anything beyond a bare
predicate is rejected before it reaches the database.

COLUMNS YOU MAY REFERENCE (nothing else exists here)

  category        text     a category id, e.g. 'jacket'. See categories above.
  colors          text[]   lower-case colour names, e.g. {'navy','white'}
  brand           text     free text, may be NULL
  warmth_rating   int      1 = very light ... 5 = heaviest, may be NULL
  formality       text     'casual' | 'smart_casual' | 'formal' | 'athletic'
  tags            text[]   free-form lower-case labels, e.g. {'date-night'}
  notes           text     free text, may be NULL
  fields          jsonb    category-specific fields; read with fields->>'name'

OPERATORS YOU MAY USE

  = <> < <= > >=            comparison
  AND  OR  NOT  ( )         boolean structure
  IN ('a','b')              a list of literal values, never a subquery
  BETWEEN 2 AND 4           inclusive range
  IS NULL / IS NOT NULL     brand, warmth_rating, formality and notes are
                            frequently NULL — check for it rather than assuming
  ILIKE '%floral%'          case-insensitive text match; the way to search notes
  tags && ARRAY['a','b']    overlaps: has ANY of these tags
  tags @> ARRAY['a','b']    contains: has ALL of these tags
  tags <@ ARRAY['a','b']    is contained by
  'navy' = ANY(colors)      the value is one of the array's elements

Rejected outright: subqueries, CTEs, UNION, casts (::), function calls of any
kind, comments, semicolons, table-qualified names, and any column not listed
above — including user_id, which is always applied for you and can never be set
from a predicate.

fields->>'key' ALWAYS RETURNS TEXT. Compare it to a quoted string:
fields->>'warmth' = '3', never fields->>'warmth' = 3. The key must be a real
field name from the category templates above; an unknown key is rejected.

Because it returns text, < <= > >= on a fields->> value compare ALPHABETICALLY,
not numerically -- fields->>'inseam_inches' > '30' is true for '9'. So on a
number-typed field use = or IN with exact values, and do the ranking yourself
from the returned items. Casts are rejected, so there is no ::numeric escape
hatch. The one numeric column you CAN order and range-compare properly is
warmth_rating, which is a real integer column.

EXAMPLES

  category IN ('jacket','coat') AND warmth_rating >= 4
  formality IN ('formal','smart_casual') AND category = 'shirt'
  tags && ARRAY['date-night','floral']
  category = 'shoes' AND fields->>'shoe_type' = 'boot'
  warmth_rating BETWEEN 1 AND 2 AND 'white' = ANY(colors)
  notes ILIKE '%rain%' OR tags @> ARRAY['waterproof']
  category = 'dress' AND brand IS NOT NULL AND NOT (formality = 'athletic')

PRACTICAL NOTES

At most 50 rows come back, so filter for what you actually need rather than
pulling the closet and sorting it yourself. colors, tags and notes are returned
in full and deliberately so — they carry the detail that distinguishes two
otherwise identical items ("floral", "multi-color", "has a hole in the pocket"),
so read them before recommending.

Prefer a slightly loose predicate over a very tight one. An empty result costs
another call out of a small daily budget; a handful of extra rows costs nothing,
because narrowing them down is something you can do yourself without asking the
server again.

If you only want one category, call list_category_items instead. If you only
want a sense of what the closet holds, call get_closet_summary. Neither takes a
predicate, and both are cheaper to get right."""


BATCH_ADD_ITEMS_DESCRIPTION = """\
Add several closet items in ONE call, from one user message.

Use this whenever the user describes more than one garment at a time. "Add three
t-shirts — a red Nike one, a blue polo and a black hoodie" is a single call to
this tool with three item objects, not three calls to add_item. The per-user
daily call budget is small, and this is the main way to stay inside it.

Before calling this tool, call get_closet_structure first if you have not
already done so in this conversation, so you know each category's id and which
fields it requires.

Parse the user's message into one structured item object per garment they
described, each in the same shape add_item takes. If a category has a required
field and the user's message does not say what it is and it cannot reasonably be
inferred from what they said, ASK THE USER for it before calling this tool — do
not guess at it and do not quietly leave it out. Inferring "navy" from "dark
blue" is reasonable; inventing a brand or a warmth rating the user never
mentioned is not. Call this tool once, with the full list, when you have enough
information for every item.

Up to 20 items per call. Items are processed independently and the result tells
you, per item, whether it was created or why it failed — an unknown category, a
missing required field, or the 200-item closet cap being reached partway
through. This is deliberately partial success: earlier items are kept when a
later one fails. Report back accurately on what that result says, naming the
items that did not go in and why, rather than implying the whole batch
succeeded or the whole batch failed."""


QUERY_CLOSET_ITEMS_DESCRIPTION = """\
Filter the closet with a SQL boolean predicate and get back the matching items.

This is the general-purpose tool for occasion- and weather-driven questions —
"something warm for a walk", "what could I wear to a formal dinner" — where the
filter is more than just a category.

The where_clause is a bare WHERE-clause predicate: no SELECT, no FROM, no
semicolon. The full grammar, the eight columns you may reference and worked
examples are in the query_instructions field returned by get_closet_structure;
call that first if you have not this conversation. A predicate that steps
outside the grammar is rejected before it reaches the database, and the
rejection tells you what to fix.

Returns up to 50 items with their colors, tags and notes included — read those,
they carry the detail that distinguishes two otherwise similar garments.

If you only want a single category, list_category_items is simpler and needs no
predicate. If you want an overview of the whole closet, use get_closet_summary.

Remember the server has no weather data and does no outfit reasoning: work out
the conditions yourself, turn them into a predicate, then choose between what
comes back and explain the choice."""


GET_CLOSET_STRUCTURE_DESCRIPTION = """\
The map of the closet's taxonomy. Call this once, early, in any conversation
that will read from or write to the closet.

Returns every category with its id and display name, the category-specific
fields each one supports (name, type, whether it is required, and the allowed
values for enums), the six common fields every item has, and query_instructions
— the exact grammar for query_closet_items, with examples.

Reading it first is what stops the two most common failures: inventing a
category id the server does not have, and guessing at query syntax that gets
rejected. Both cost a call out of a tight daily budget. It takes no arguments
and does not depend on how much is in the closet."""


ADD_ITEM_DESCRIPTION = """\
Add ONE garment to the closet.

If the user described more than one item in a message, use batch_add_items
instead — it does the whole lot in a single call, and the daily call budget is
small.

category must be a category id from get_closet_structure. Fill in whatever the
user actually told you: colors (up to 5, lower case), brand, warmth_rating
(1 = very light, 5 = heaviest), formality, tags, and notes for anything that
does not fit a structured field. fields carries the category-specific values —
shoe_type for shoes, sleeve_length for a shirt, and so on.

Do not invent values. If the category requires a field and the user did not give
you enough to fill it in, ask them rather than guessing. Rejected if the closet
is already at its 200-item cap."""


UPDATE_ITEM_DESCRIPTION = """\
Change an existing item. Only the fields you supply are touched; everything else
is left as it is.

You need the item's id, which comes back from query_closet_items,
list_category_items, add_item or batch_add_items. Do not guess at one.

Changing category re-validates fields against the new category's template, so a
value that made sense on the old category may need to be supplied again or
dropped. To clear tags or colors, pass an empty list."""


REMOVE_ITEM_DESCRIPTION = """\
Delete one item from the closet, permanently.

You need the item's id from one of the read tools. Deletion cannot be undone, so
if there is any ambiguity about which item the user means — two black t-shirts,
say — confirm which one before calling this. There is deliberately no tool for
deleting the whole closet or the account; that lives in the web app behind an
explicit confirmation step."""


LIST_CATEGORY_ITEMS_DESCRIPTION = """\
Every item in one category, up to 100, with all their fields.

The right tool for "what shoes do I have" or "show me my jackets". It takes a
category id from get_closet_structure and no predicate at all, so there is no
query syntax to get wrong. Prefer it over query_closet_items whenever the filter
really is just the category."""


GET_CLOSET_SUMMARY_DESCRIPTION = """\
An overview of the closet without pulling every item: the total count, how many
items are in each category, the distinct brands and tags in use, and the spread
of formality and warmth ratings.

The right tool for "what's in my closet" and the cheapest way to orient yourself
before a recommendation — one call here often saves several exploratory
query_closet_items calls out of a tight daily budget. Use the tags it lists to
build predicates that will actually match, rather than guessing at tag names."""


GET_USER_PROFILE_DESCRIPTION = """\
The user's home location and whether they prefer Fahrenheit or Celsius.

Call this before asking where someone lives — that is what it is for. Weather is
still yours to find; this just saves re-asking for the location every
conversation. home_location may be null if they have not set one in the web app,
in which case do ask."""
