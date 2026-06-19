# An Agentic AI for Process Flowsheet Design and Optimization in DWSIM

*Draft manuscript front/back matter. The detailed Methods and Results are in
`THESIS_DRAFT_METHODS_RESULTS.md`; this file holds the Abstract, Introduction,
Discussion, and Conclusion that frame them into a complete submission for*
Computers & Chemical Engineering. *Every claim is traceable to a committed,
reproducible artifact in the repository.*

---

## Abstract

Process flowsheet design and optimization remain expert-, time-, and
licence-intensive, and the dominant tools are closed commercial suites. We
present an agentic AI that designs, builds, solves, and optimizes process
flowsheets in the open-source simulator DWSIM directly from natural language.
The system couples a tool-using large-language-model (LLM) agent — a
reason→act→observe loop over a 107-tool action space, with proactive
retrieval-augmented generation, a safety validator, and provider-agnostic
failover — to the DWSIM `.NET` engine through a `pythonnet` bridge that performs
atomic build-and-solve, recycle auto-tearing, energy-stream injection, and
read-back-after-write verification. On top of a broad solver stack (CMA-ES,
NSGA-II, NLopt, Bayesian/EGO, SALib global sensitivity) we contribute five
capabilities that target the gap between a black-box wrapper and a commercial
optimizer: a derivative-free trust-region surrogate equation-oriented scheme, an
infeasible-path simultaneous tear-and-optimize formulation, a multi-process
parallel evaluator, a total-annualized-cost economic objective, and a
*thermodynamic-intelligence* layer that auto-selects the theory-appropriate
property package and **measures** thermodynamic model-form uncertainty rather
than claiming fidelity parity. We validate at three levels of increasing realism:
558 component tests; exact agreement with closed-form/analytic optima for the
optimizer stack (single-objective 5/5, NSGA-II front to 2×10⁻⁵, Sobol to 1×10⁻³);
and live demonstrations on a DWSIM v9.0.5 engine, including a capstone case study
— two-stage compression with intercooling — in which the agent's optimizer drives
a real flowsheet to an *interior* optimum that matches the textbook closed-form
value (P\*=√(P₁P₂)=3.162 bar) via three independent methods (analytical 3.162,
parametric sweep 3.000, optimizer 3.170). We position the contribution honestly
as **Aspen-level optimization *methodology* over an open simulator**, not
"Aspen-level simulation": two gaps — thermo fidelity and true native
equation-oriented optimization — are fundamental to building *on* DWSIM and are
stated, with the uncertainty one *quantified* rather than hidden. The full system,
a content-hashed 25-task benchmark, and a fully-wired component-attribution
ablation harness are released for reproducibility.

**Keywords:** agentic AI; large language models; process simulation; DWSIM;
flowsheet optimization; equation-oriented optimization; thermodynamic model
selection; uncertainty quantification; reproducibility.

---

## 1. Introduction

Building and optimizing a process flowsheet is a core chemical-engineering task
that still demands an expert driving a complex GUI: selecting unit operations and
a thermodynamic package, specifying streams, converging recycles, and running
sensitivity or economic studies. The leading tools (e.g. Aspen Plus) are
powerful but closed, licensed, and not programmable by non-experts in natural
language. Recent tool-using LLM agents can plan and act over external tools, but
applying them to *physics-grounded* process simulation raises three problems this
paper addresses: (i) reliably translating natural-language intent into a
correct, convergent flowsheet on a real engine; (ii) bringing
*commercial-grade optimization methodology* to an open simulator that does not
expose its equations; and (iii) doing so **honestly** — neither overclaiming
fidelity parity nor hiding where an open engine is fundamentally behind.

We target DWSIM, a mature open-source steady-state simulator, and build an agent
that operates it end-to-end. Our position throughout is one of calibrated,
auditable claims: we separate *engineered capability*, *method validated against
a known answer*, and *demonstrated on the live engine*, and we report the
boundaries plainly.

**Contributions.**
1. **An end-to-end agentic system** that designs, builds, solves, and optimizes
   real DWSIM flowsheets from natural language, with a robustness-hardened bridge
   (atomic build/solve, recycle auto-tearing, energy-stream injection,
   read-back-after-write verification, dirty-state tracking) and a safety/quality
   guard layer.
2. **Five optimization/analysis capabilities** that close specific gaps to a
   commercial optimizer — trust-region surrogate EO, infeasible-path SQP, a
   multi-process parallel evaluator, a total-annualized-cost objective, and a
   thermodynamic-intelligence layer — each engine-agnostic and validated against
   known answers.
3. **A measured, not asserted, treatment of thermodynamic fidelity:** the system
   auto-selects the theory-appropriate property package from a registry of the 28
   packages DWSIM installs (mapped to Aspen method names with their gaps stated)
   and quantifies model-form uncertainty by solving under the credible
   alternatives — turning the field's standard criticism of open-source thermo
   into a reported number.
4. **A reproducibility package:** 558 component tests, a content-hashed 25-task
   benchmark, append-only ReAct transcripts, and a fully-wired four-condition
   ablation harness (verified end-to-end without quota) with non-parametric
   statistics, so the component-attribution study is one command from results.
5. **A live capstone result:** the agent's optimizer driven to an interior
   optimum with a closed-form answer on a real DWSIM flowsheet, matched three
   independent ways — the strongest single piece of optimizer-coupling evidence.

The remainder synthesises the methods (§2 of `THESIS_DRAFT_METHODS_RESULTS.md`),
the three-level evaluation and its results (§2 there), and closes with an honest
discussion of validity and the path to the full headline study.

---

## 6. Discussion, Limitations, and Conclusion

**What is established.** The system is designed, implemented, component-validated
(558 tests), and demonstrated end-to-end on a live DWSIM v9.0.5 engine. Its
optimization *methodology* is at commercial level and validated against known
optima — most strongly by the compression capstone, where a live flowsheet is
driven to an interior optimum that matches a closed-form result via three
independent methods, isolating the optimizer-plus-engine coupling from any tuned
parameter.

**What is fundamentally limited.** Two gaps are not closable by code because the
system builds *on* DWSIM rather than replacing it: (a) **model/thermo fidelity** —
DWSIM's thermodynamics are not validated against decades of industrial data the
way a commercial databank is; and (b) **true native equation-oriented
optimization** — DWSIM does not expose its equation system, so the EO here is a
validated surrogate, not open-equation. We do not claim "Aspen-level simulation."
Crucially, gap (a) is *measured*: the thermodynamic-intelligence layer reports how
much any result depends on the package choice, converting a blanket criticism into
a per-result uncertainty.

**Threats to validity.** The live 25-task benchmark headline (24 % strict, 32 %
over executed tasks) is quota- and scoring-limited, not a clean capability number;
the component-attribution ablation that explains *why* the architecture works is
fully wired and verified but its statistical results await LLM throughput; the
parallel speed-up is workload-dependent (CLR-init bound, corrected from an earlier
mock-based figure); and an infeasible-path *coupling-correct* live recycle remains
open. LLM stochasticity is controlled in the ablation by a provider lock and
temperature 0, with the LLM-as-judge used only as a secondary signal alongside
deterministic physics-based criteria.

**Conclusion.** Natural-language agentic operation of an open process simulator is
feasible and, for optimization *methodology*, can reach commercial level while
remaining fully open and reproducible. The honest, defensible claim is *an
agentic AI delivering Aspen-level optimization methodology over DWSIM, with the
thermodynamic-fidelity gap measured rather than hidden.* The remaining
evidentiary gaps close with higher LLM throughput and **no new code** — the
ablation and full benchmark are one command each — making the result a clean
basis for both this submission and a follow-on study.
