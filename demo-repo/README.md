# causeway-demo

A repository that follows the [Causeway](https://github.com/) demo contract
(`causeway.json`), so Causeway's Milestone 6 GitHub-ingestion path has a real,
public repository to investigate.

This is **not** a real production service. It is the same controlled
order-service incident Causeway bundles for its own demo, checked in here so
Causeway can clone it, validate it against the contract, and run the actual
causal investigation and fix loop against code that came from GitHub rather
than from Causeway's own checkout.

## What's here

- `causeway.json` - the manifest. Declares the service name, runtime,
  entrypoint, workload fixture, the incident's deploy history, and the one
  repair surface Causeway is allowed to patch in a disposable sandbox copy.
- `service.py` - the order-service itself. Standard library only, on
  purpose: Causeway launches this as a subprocess, never imports it, and
  never installs a dependency on its behalf.
- `fixtures/incident-001.json` - the recorded request workload replayed
  identically across every experiment phase.

## Publishing this repository

To make this the live demo repository Causeway points at:

```
cd demo-repo
git init
git add .
git commit -m "Causeway demo repository"
git branch -M main
git remote add origin https://github.com/<your-account>/causeway-demo.git
git push -u origin main
```

Then paste `https://github.com/<your-account>/causeway-demo` into Causeway's
repository field and run the investigation.

## Expected result

- Observational ranking: A ranks above B (the large refactor looks more
  suspicious than the three-line predicate change)
- Controlled experiment: A -> REFUTED, B -> PROVEN
- Fix loop: B's proposed fix -> VERIFIED in a disposable sandbox copy

Nothing here is ever pushed to, committed to, or deployed from this
repository by Causeway. A verified fix is shown for human review only.
