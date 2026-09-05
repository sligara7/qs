# reflow2 feedback from the qs project

Written 2026-09-05 by the agent that used reflow2 through the whole qs design and build (genesis
on 2026-09-04, then roughly 40 commits, three test beds, and about 350 design nodes). This is
feedback about the tool, not about qs. Where a point rests on one incident I say so.

## What worked well

- **Decisions survived the session.** The conversation was compacted at least once. Every choice
  the user made (engine adoption, HTTP only, stop-and-wait, reference-not-vendor, the three
  brainstorm picks, logging) came back intact from the graph, with its rationale and the options
  that lost. Nothing had to be re-argued.
- **Brainstorm to decision is a good shape.** Recording an idea as an *exploratory* decision with
  the options and counter-arguments, then revising the same node into a *choice* when the user
  picked, kept one home per topic. The preserved-on-write snapshot meant the original brainstorm
  text was never lost when the node was rewritten.
- **Verification findings as evidence.** Putting what a run *found* (numbers, PV names, timings,
  which sim limit stopped what) on the verification rather than in a chat message paid off
  repeatedly: later in the day the findings were the only place the earlier measurements existed.
- **Near-duplicate refusal.** `add_requirement` / `add_capability` / `add_decision` refusing when a
  close node existed caught two real would-be duplicates and forced a deliberate `distinct_from`.
  It was never wrong in this project.
- **Change events plus checksum dispositions.** `add_change_event` naming the affected artifacts
  and `set_artifact_checksum ... design_updated` gave a real code-to-design thread. The
  `design_holds` / `documentation` distinction was cheap to honour and reads well afterwards.
- **`loop_status` as the one cheap question.** Knowing at any moment what the loop was owed kept
  the graph from silently rotting between bursts of code.
- **`unclaimed_findings`.** The session-scoped "what did I make false" question is the right
  question. Here it returned nothing, and `subjects_examined: 20` made that a meaningful nothing.
- **Served skills with a lens.** `capture-session` and `brainstorm` arriving from the server with the
  contributor background attached meant the vocabulary matched the user (a systems engineer) without
  extra prompting.
- **`acknowledge_gap` with a reason.** Being able to say *why* a gap is acceptable, and have it
  expire if the affected nodes change, is the right answer to "the open list must be able to reach
  zero".

## Gaps and issues met

- **Hashes by hand, about forty times.** `link_artifact`, `set_artifact_checksum` and
  `reconcile_artifacts` all take a checksum the caller computes. reflow2 does no file I/O by
  design, which is defensible, but the practical effect was a shell command and a paste per
  artifact per change. The reconcile sweep at the end needed a hand-built table of 69 ids to paths
  and a script to hash them. A companion command (`reflow2 hash <paths>` or a skill that emits the
  `observed` list) would remove the most repetitive work of the day.
- **Many small calls per fact.** Recording one verification took four to six calls
  (`add_verification`, `set_verification_status`, `verifies` per target, `link_artifact` per file).
  Recording one decision the user had already made took `add_decision` then `set_decision_status`,
  and doing both in one parallel batch is refused because they are unordered. `add_capability`
  accepts `status`; `add_decision` and `add_requirement` do not accept an initial status even when
  the owner's word is in hand. A `decided_by` / `accepted_by` field, or `verifies`/`allocate`
  lists on the constructors, would halve the traffic.
- **No `partial` verification status.** An acceptance run that proved everything qs is responsible
  for but could not exercise one plan (blocked by the simulator) had to be either `passing` or
  `blocked`. I chose `passing` with a long findings paragraph; the status alone misleads either way.
- **DesignRule has no constructor.** It had to be created through the generic `create_node`; the
  `rationale` I wrote was reported as undeclared, and `enforced` defaults to true, so a rule
  recorded without thought claims to gate merges. `find_tools("design rule convention")` did not
  surface anything relevant, which is a discoverability miss on top of the missing constructor.
- **Wiring debt arrives as several separate findings.** A new capability that is satisfied, verified
  and realised but not yet *allocated* produces an "unallocated" gap, an "unrealized" gap (when the
  artifacts point at the component instead), and an "unthreaded cluster" defect. All three are
  correct, but they are one missing edge seen from three angles, and the repair guidance for the
  cluster ("no mechanical repair, a judgement only a person holds") is heavy for what was a
  forgotten `allocate` call.
- **External test beds have no node type.** The simulated beamline, hex-profile-collection and
  hextools were central to this project (the acceptance layer depends on them, fixes went into
  them) but the graph has no place for "a repository we depend on but do not own". I attached
  their configs and shims to Verifications and put the fixes into a Decision's text. There is an
  `external_dependency` tool I did not try; if it fits, the skills should point at it.
- **Verbose replies.** Every reply carries `loop_hint`; revisions echo the entire prior text of
  the node; `detect_defects` sends the per-category repair essay; several tool descriptions cite
  internal backlog numbers and measurements from reflow2's own design that mean nothing here.
  Over a long session this is a real token cost. A `brief` option on writes (return ids and a
  one-line delta) would help.
- **Acknowledgements without a name.** Every `acknowledge_gap` warned that it carried nobody's
  name. A Contributor for the user existed (the lens named it) and I did not pass it; the tool
  could default `approver` to the session's contributor rather than warn each time.
- **Validation as a separate kind.** The "20 verified capabilities not validated" gap is a good
  concept (built right vs the right thing) but it fires as one block the moment a design has
  verifications, before any validation is possible. A grace condition (only after a release or an
  epoch) would make it land when it can be acted on.
- **Parked ideas.** Exploratory decisions connect to nothing by construction, so each one raises
  an "open idea connects to nothing" gap until the user picks. Reasonable, but it means the gap
  list grows with every brainstorm; a `kind: exploratory` node could be exempt until it becomes a
  choice.

## Ideas for improvement or new features

1. **Hash and reconcile helper.** A CLI or skill that walks the registered artifacts, hashes what
   is on disk, and calls `reconcile_artifacts` with the observed list, so a session ends with one
   command instead of a hand-built table.
2. **Constructors that accept the owner's word.** `add_decision(..., status="accepted",
   decided_by=<contributor>)` and the same for requirements, refused unless a contributor is named,
   so the certainty rule still holds but one call does the work.
3. **`add_verification` with targets and a first run.** `verifies=[...]`, `status`, `findings`,
   `last_run_at` in the constructor. The common case is "I just ran it and here is what it found".
4. **A "wire this capability" call.** Allocate, realizes and verifies in one step for a capability
   that already has its component, artifacts and check known. It would remove the three-finding
   pattern above.
5. **Verification status `partial`,** or per-clause findings, for checks that pass for the part
   under the project's control and are blocked elsewhere.
6. **A test-bed / external repository node.** With a pinned ref, a bring-up recipe, and a place to
   record fixes pushed into it. The acceptance layer of any project that tests against a simulator
   or a shared library needs it.
7. **A "decisions awaiting the owner" view scoped to a session.** I kept that list by hand in the
   final report each time. `loop_status` counts assigned decisions but not the proposed exploratory
   ones the user has been asked about in conversation.
8. **A plain-language walkthrough of gaps and defects.** The user asked for exactly that and I
   produced it manually; the data is already there, a rendering that speaks the owner's domain
   (requirement, check, part) instead of edge names would be a direct win.
9. **Brief reply mode.** `verbosity: brief` on write tools, and the repeated hint text sent once
   per session rather than once per call.
10. **Session digest export.** `capture-session` is manual and the skill itself says it should not
    have to be. A tool that lists what this session added, changed and closed in the graph, in
    plain language, would make the end-of-session capture a review rather than a reconstruction.
