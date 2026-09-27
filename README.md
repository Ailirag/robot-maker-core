# robot-maker-core

A verification core for CAD and hardware projects: the part of a working robot
repository that has nothing to do with that robot.

It is the machinery that refuses to take a number on trust. Dimensions are facts with a
recorded provenance and a datum, not literals. Results live in immutable evidence
directories that are bound by hash, so a report cannot quietly describe a different
geometry than the one it was computed from. Geometry is screened for thin walls, small
sections and narrow connections, and every finding has to be decided by a human reason
before anything downstream will proceed. Print jobs are packaged, sliced natively and the
slicer's own output is validated against what was prepared. Assemblies are swept for
insertion fit; cables get a routing contract; instructions are generated from the same
bound snapshot they document.

## What this is not

This is a snapshot of the reusable core extracted from a private robot project. It is not
a framework, it has no stable API, and it carries no design of its own — every model, every
measurement and every part of that robot was left behind.

The extraction is mechanical and verifiable rather than curated: a module is here only if
it names no design, imports nothing from the project's `cad/` tree, and needs nothing
outside this set to run. Modules that failed any of those three were dropped even when they
looked generic, which is why some obvious entry points (a document regenerator, a design
registry, a commit guard) are absent — each reached back into project glue.

## What is here

| | |
|---|---:|
| modules | 92 |
| test files | 56 |
| tests passing in isolation | 641 (2 skipped) |

```
engineering/
  facts.py, fact_closure.py, fact_extension.py   facts with provenance, unit, datum, scope
  delivery.py, evidence_contract.py              hash-bound reads; immutable evidence
  weakness_scan.py, structural_scan.py           thin walls, sections, narrow connections
  thin_sample_context.py                         replaying a finding's own samples
  geometry_feedback.py                           a finding is closed by a reason, not a flag
  assembly_fit.py, assembly_sequence.py          insertion sweeps and assembly order
  print_package.py, multiplate_job.py            plate packaging and native slice validation
  print_risks.py, print_strength.py              per-region print decisions
  cable_routing.py, wiring_topology.py           conductor inventory and routing contract
  instructions/                                  manual contract, guides, offline viewer
  nightly/                                       scheduled verification plumbing
```

## Running the tests

```bash
python -m pytest -q tests
```

The suite is the proof that this set stands alone: it was reduced until every test in it
passes from a clean directory with nothing else present.

## Provenance

Extracted on 2026-09-27 from a private repository, as a snapshot without history. The
private project keeps the design, the measurements and the project-specific glue.

## License

MIT — see [LICENSE](LICENSE).
