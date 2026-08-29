# 🌉 Causeway — AI-Powered Causal Root Cause Analysis

> **AI proposes. Code validates. Measurements decide.** ⚡

Causeway is an **AI-powered incident investigation system** that goes beyond simply guessing the root cause of a production incident.

Instead of saying *“Deployment B probably caused the problem,”* Causeway **tests the hypothesis through controlled experiments**, replays the incident, measures the system's behavior, and determines whether the suspected change actually caused the failure.

---

## ✨ Key Features

### 🚨 1. Incident Detection

Detects abnormal application behavior such as sudden increases in API latency.

```text
Normal latency  →  20 ms
Incident latency → 330 ms 🚨
```

---

### 🔍 2. Candidate Localization

Analyzes recent deployments and identifies changes that could be responsible for the incident.

```text
Deployment A
🟡 9 files changed
🟡 412 lines changed

Deployment B
🔴 1 file changed
🔴 3 lines changed
```

Causeway doesn't assume that the biggest or most suspicious change is the actual cause.

---

### 🤖 3. AI-Powered Experiment Planning

Gemini analyzes the incident and candidate changes to create a **testable hypothesis** and experiment plan.

Instead of asking:

> ❌ "What caused this incident?"

Causeway asks:

> ✅ "What experiment can prove or disprove this hypothesis?"

---

### 🛡️ 4. Experiment Validation

AI-generated experiments are passed through a deterministic validator before execution.

```text
🤖 Gemini
    ↓
📋 Experiment Plan
    ↓
🛡️ Validator
    ↓
✅ Safe & Valid?
    ↓
🧪 Execute
```

This prevents the AI from directly performing unsafe or invalid actions.

---

### 🧪 5. Controlled Sandbox Experiments

Experiments are performed in an isolated environment rather than directly affecting production.

This allows Causeway to safely test hypotheses without disrupting real users.

---

### 🔄 6. Incident Replay

Causeway replays the **same workload** during experiments.

```text
Original Incident
       ↓
📦 Capture Workload
       ↓
🔁 Replay
       ↓
🧪 Experiment
```

Using the same workload makes the comparison more reliable because the tested variable is changed while other conditions remain as consistent as possible.

---

### ⚡ 7. Candidate Ablation

Causeway temporarily removes the suspected change and observes what happens.

Example:

```text
B = ON
   ↓
🐌 333 ms

B = OFF
   ↓
⚡ 21 ms
```

If removing B makes the system recover, B becomes a strong causal candidate.

---

### 🔁 8. Restore Testing

Causeway then restores the suspected change.

```text
B ON  → 🐌 333 ms
B OFF → ⚡ 21 ms
B ON  → 🐌 331 ms
```

If the failure returns after restoration, the evidence for causality becomes significantly stronger.

---

### 📊 9. Performance Measurement

Causeway measures system performance before, during, and after experiments.

It also uses repeated measurements to reduce the influence of random performance fluctuations.

```text
Control      → 23 ms
Reproduce    → 333 ms 🚨
Ablation     → 21 ms  ⚡
Restore      → 331 ms 🚨
```

---

### 🎯 10. Causal Verdicts

Causeway classifies hypotheses based on experimental evidence.

| Verdict          | Meaning                                                                           |
| ---------------- | --------------------------------------------------------------------------------- |
| 🟢 **PROVEN**    | Removing the candidate fixes the failure and restoring it brings the failure back |
| 🔴 **REFUTED**   | Removing the candidate does not eliminate the failure                             |
| 🟡 **SUPPORTED** | Evidence points toward the candidate, but is not strong enough for a full proof   |
| ⚪ **UNRESOLVED** | Measurements are too unstable or ambiguous to reach a reliable conclusion         |

---

# ⚙️ How Causeway Works

Causeway follows a complete **observe → reason → experiment → verify** pipeline.

```text
🚨 PRODUCTION INCIDENT
          │
          ▼
📡 COLLECT TELEMETRY
 Logs • Metrics • Traces
          │
          ▼
🔍 LOCALIZE CANDIDATES
 Recent Deployments
          │
          ▼
🤖 GEMINI
 Generate Hypothesis
 + Experiment Plan
          │
          ▼
🛡️ VALIDATOR
 Check Experiment
          │
          ▼
🧪 SANDBOX
          │
          ▼
🔁 REPLAY INCIDENT
          │
          ▼
✂️ ABLATE CANDIDATE
          │
          ▼
📊 MEASURE
          │
          ▼
🔄 RESTORE CANDIDATE
          │
          ▼
📊 MEASURE AGAIN
          │
          ▼
🎯 CAUSAL VERDICT
```

---

# 🧠 The Core Concept

Traditional Root Cause Analysis often looks like:

```text
📡 Logs + Metrics
       ↓
      🤖 AI
       ↓
"Probably Deployment B"
```

Causeway takes a different approach:

```text
📡 Evidence
    ↓
🤖 AI Hypothesis
    ↓
🧪 Controlled Experiment
    ↓
📊 Real Measurements
    ↓
🎯 Causal Verdict
```

The AI **doesn't get to decide the answer**.

It proposes what should be tested.

The system then tests that hypothesis against actual behavior.

---

# 🔬 Example: Finding the Real Culprit

Imagine two deployments happened shortly before an incident.

### Deployment A

```text
📦 refactor/order-query-batching
9 files
412 lines changed
```

### Deployment B

```text
📦 perf/normalise-audit-predicate
1 file
3 lines changed
```

The incident:

```text
🚨 Order API p95 latency

20 ms → 330 ms
```

A simple correlation-based system might rank **A** higher because it is a larger and more significant-looking change.

Causeway doesn't stop there.

### 🧪 Test A

```text
A = OFF
B = ON

Latency → 316 ms
```

The incident remains.

```text
A removed
     ↓
Failure remains
     ↓
❌ A REFUTED
```

### 🧪 Test B

```text
A = ON
B = OFF

Latency → 21 ms ⚡
```

The system recovers.

Now restore B:

```text
A = ON
B = ON

Latency → 331 ms 🚨
```

The failure returns.

```text
B ON
 ↓
🐌 SLOW

B OFF
 ↓
⚡ FAST

B ON
 ↓
🐌 SLOW
```

### 🎯 Result

```text
A → ❌ REFUTED
B → 🟢 PROVEN
```

A large change was innocent.

A tiny **3-line change** was responsible.

That's the problem Causeway is designed to solve.

---

# 🤖 What Does Gemini Do?

Gemini acts as the **reasoning layer**.

It can:

* 🧠 Analyze incident evidence
* 🔎 Examine candidate changes
* 💡 Generate hypotheses
* 🧪 Design controlled experiments
* 📝 Produce structured experiment specifications
* 🔧 Propose potential fixes

But Gemini **does not determine the final causal verdict**.

The architecture is:

```text
🤖 AI PROPOSES
       ↓
🛡️ CODE VALIDATES
       ↓
🧪 SYSTEM EXPERIMENTS
       ↓
📊 MEASUREMENTS DECIDE
```

---

# 🏗️ System Architecture

Causeway can be viewed as three major layers:

### 📡 Layer 1 — OBSERVE

```text
Logs
Metrics
Traces
Deployments
Database Activity
```

**Question:**

> What's happening?

---

### 🧠 Layer 2 — REASON

```text
Gemini
   ↓
Hypotheses
   ↓
Experiment Plans
```

**Question:**

> What could explain the incident, and how can we test it?

---

### 🧪 Layer 3 — VERIFY

```text
Sandbox
   ↓
Intervention
   ↓
Replay
   ↓
Measurement
   ↓
Verdict
```

**Question:**

> Does reality agree with the hypothesis?

---

# 🌟 Why Causeway?

Most incident investigation systems focus on **correlation**.

Causeway focuses on **causal verification**.

Instead of:

> "This deployment happened before the incident."

Causeway asks:

> "What happens if we remove this deployment while keeping everything else as controlled as possible?"

That distinction turns an **AI-generated guess** into an **experimentally supported explanation**.

---

# 🚀 Vision

The long-term vision for Causeway extends beyond identifying the root cause.

```text
🚨 INCIDENT
     ↓
🔍 INVESTIGATE
     ↓
🎯 VERIFY ROOT CAUSE
     ↓
🤖 GENERATE FIX
     ↓
🧪 APPLY FIX IN SANDBOX
     ↓
🔁 REPLAY INCIDENT
     ↓
📊 VERIFY RECOVERY
     ↓
👤 HUMAN REVIEW
     ↓
🚀 PRODUCTION
```

The goal is an intelligent incident-response system that can **investigate, experiment, generate fixes, and verify those fixes** while keeping humans in control of production changes.

---

## 💡 Core Philosophy

> **Don't just guess the root cause. Test it.**

### 🌉 Causeway

**From correlation → to causation.**
**From AI guesses → to experimental evidence.**
**From incident detection → to verified resolution.**

---
