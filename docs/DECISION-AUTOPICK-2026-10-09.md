# Decision — the app chooses a contrast by default

**Decided:** 2026-10-09, Puck. **Status:** settled. **Reverses:** the refusal
recorded in `atlas_index.picker_payload`'s docstring.

> *"i would rather it choose by default. and they can be picked if a use wants.
> this should be the philosophy. design hidden behind first viewing"*

## What was refused, and by whom

`picker_payload` said:

> **There is no `selected` or `recommended` field and there will not be one.**
> The spike's verdict is that no sensible default exists — 101 of 138 Crohn's
> contrasts come from one ustekinumab trial — and a default pick in a payload is
> a recommendation whatever the UI calls it (rule 7, COUNCIL V10 by analogy).

That measurement is still true. Confirmed live on 2026-10-09: dominance is
**101 of 138 from E-GEOD-100833**.

## What changed

Not the measurement — the **goal**. The refusal optimised for not implying an
opinion. Puck's stated philosophy optimises for a simple first view with the
detail reachable behind it, and on that goal a 138-row table presented cold is
the worse failure: it is not neutral, it is unusable, and a reader resolves it
by picking the first drawable row rather than by reading 138.

The old refusal and the new philosophy disagree about which risk matters more.
That is a judgement about the product, and it is the owner's to make.

## What is reversed

The app picks a contrast on first view and shows it, with its reasoning.

## What is NOT reversed, and must stay that way

1. **`picker_payload` is still neutral.** No `selected`, `recommended`,
   `default`, `best` or `suggested` key, asserted by
   `test_there_is_still_no_default_pick`. The listing describes; it does not
   bless. The pick comes from `/api/autopick`, a separate action.
2. **The full table stays, and stays complete.** All 138 rows, nothing removed,
   nothing hidden — behind a disclosure, not behind a filter. `_flags`' rule is
   unchanged: a picker that dropped the 121 microarray rows would leave a reader
   believing Atlas has 17 Crohn's contrasts.
3. **The pick is auditable or it does not ship.** Every pick carries the stated
   rule as a sentence, the candidates it chose among, and every excluded row
   with a reason. A reader must be able to reconstruct the choice without
   reading the code.
4. **The chooser combines nothing.** One number from one source
   (`min(n_ref, n_test)`), ties broken deterministically. A chooser scoring
   contrasts on several axes is the machinery rule 7 removed, rebuilt one level
   upstream, and `test_it_combines_nothing` scans the executable method for it.
5. **An empty candidate set refuses.** It never resolves to the least bad row.
6. **The control side is characterised.** "Control" in Atlas is frequently not a
   healthy volunteer; which kind it is changes what every log2FC means, and a
   reader handed a pick does not read the label the way a reader choosing by
   hand does.

## The risk this accepts, stated plainly

A wrong contrast produces a complete, internally consistent, entirely wrong
figure, with every invariant passing, because nothing downstream can tell the
gene set came from the wrong experiment. Choosing by hand, a reader saw the rows
they chose between; choosing by default removes that.

The mitigation is not that the rule is good. It is that the figure already
prints the contrast identity, both group labels, the sign sentence and n — and
that the pick arrives with its reasoning attached rather than silently.

Concretely: on the Crohn's index the rule chooses **E-GEOD-83687 g2_g1**
(61 v 43, control "normal"), while the runs made by hand in this session used
**E-GEOD-101794 g2_g1** (13 v 61). The rule optimises sample size and knows
nothing about tissue, Paris classification, or which paper the reader cares
about. That divergence is expected, is not a defect, and is exactly why the
"choose a different contrast" path stays one click away.

## Still open

- **A model-backed chooser.** The stated goal is for a model to weigh what a
  rule cannot — tissue relevance, control quality, study design. `Chooser` is a
  protocol and a model implementation is a drop-in, but it is a **third paid
  call** and AGENTS.md rule 8 permits two, with two separate $5 caps. That
  amendment is Puck's and has not been made.
- **Sources beyond Expression Atlas.** Atlas is an open database of published
  experiments and ships a computed differential result, which is what makes it
  usable here. Most repositories do not: sourcing from GEO or from paper
  supplementary tables means parsing heterogeneous tables or running the
  differential analysis in-app, and neither is in scope under this decision.
