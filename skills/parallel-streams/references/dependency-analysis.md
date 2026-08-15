# Finding dependencies between streams

The whole value of a split is here. Everything else is formatting.

A wrong split does not fail loudly — it fails three hours later, as a merge conflict in a file two
sessions both rewrote, or as two sessions that built the same helper twice under different names.

## The one distinction that matters

**Step order inside a plan is not a dependency between streams.** Plans are written as a reading
sequence: chapter 2 follows chapter 1 because that is how a human reads. That says nothing about
whether the two can be built at the same time.

Ask instead: *if both sessions started right now, what would break?*

## Count as a dependency

**Shared edit surface.** Two streams change the same file, or the same tightly-coupled module.
Both will produce a diff over the same lines, and the second merge will conflict. One waits.

**Produced artifact.** One stream needs something the other creates before it can even run:

- a database table, column, or migration
- an endpoint, an event, a queue name
- a config key, a feature flag, an environment variable
- a generated artifact: a client, a schema, a type definition, a fixture
- a new package or module that the other imports

**Single-owner version.** Both bump the same versioned thing: a schema version, a protocol
version, a lockfile, a public interface, a released package version. Two bumps of one counter
cannot be merged — the second is simply wrong. Merge those streams into one, or serialize them
explicitly and say so.

**Contract change.** One stream changes a signature, a payload shape, or an error contract that
the other stream's code calls. The caller has to be written against the new shape, so it waits.

## Do NOT count as a dependency

**Same package, different files.** Two streams working in one directory on unrelated files is
merge *risk*, not a dependency. Note it in both briefs ("stream 3 is also working in this package;
rebase before opening the pull request") and let them run in parallel. Serializing here is the most
common way a split quietly loses its point.

**Same test file.** Annoying, cheap to resolve, not a reason to wait. Say it in the brief.

**"It feels safer to do them in order."** If you cannot name what breaks, there is no dependency.

**Shared review capacity.** That is a scheduling limit, not a dependency. The map describes the
work, not the calendar.

## Traps that look independent and are not

| Looks independent | Actually blocks |
|---|---|
| Two streams add unrelated fields to the same model | Same file, same migration counter, and often the same generated types |
| One adds a setting, another reads "the current settings" | Reader has to know the key exists; without it, defaults hide the bug |
| Two streams add commands to one router / registry | Same registration file, and often the same ordering list |
| Two streams add dependencies | Same lockfile — a guaranteed conflict, and a slow one to resolve |
| One renames a concept, another writes new code using the old name | The rename must land first, or the new code is born stale |
| Two streams touch the same public interface from opposite sides | The interface is one contract with one owner |
| One writes the docs for what another builds | Docs can start, but cannot be finished or reviewed before the behavior exists |

## Cutting a cycle

If A needs something from B and B needs something from A, the split is wrong, not the plan. Fix it
one of these ways, in order of preference:

1. **Extract the shared piece into its own stream** that both wait for. This is nearly always the
   right answer, and it usually reveals the real interface.
2. **Move one item across the boundary** so that one stream owns both sides of the exchange.
3. **Merge the two streams.** Cheaper than an ordering rule nobody will follow.

Never resolve a cycle by "they will coordinate in chat". Sessions do not read each other's chats.

## Sizing check, once the groups exist

- A stream that touches more than a handful of subsystems is not one stream.
- A stream whose brief you cannot write without saying "and also, depending on what stream 4 does"
  is not independent — go back to step 2.
- A stream smaller than a single commit is not worth a session; fold it into its neighbour.
- A migration and its only consumer stay together. Splitting them buys nothing and creates a window
  where the repository is broken.
