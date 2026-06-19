# Cover Letter — *Computers & Chemical Engineering*

Dear Editor,

We submit our manuscript, **"An Agentic AI for Process Flowsheet Design and
Optimization in DWSIM,"** for consideration as a research article in *Computers &
Chemical Engineering*.

**Motivation and novelty.** Process flowsheet design and optimization remain
expert- and licence-intensive, dominated by closed commercial tools. We present,
to our knowledge, the first end-to-end agentic AI that designs, builds, solves,
and optimizes flowsheets on a real steady-state simulator (the open-source DWSIM)
directly from natural language — and, critically, brings *commercial-grade
optimization methodology* to an open engine that does not expose its equations.
The work sits squarely in the journal's scope: process simulation, optimization
algorithms, and computer-aided process engineering.

**Why it matters to the readership.** Beyond the agent itself, we contribute five
engine-agnostic capabilities validated against known answers — a derivative-free
trust-region surrogate equation-oriented scheme, an infeasible-path simultaneous
tear-and-optimize formulation, a multi-process parallel evaluator, a
total-annualized-cost objective, and a *thermodynamic-intelligence* layer that
auto-selects the property package and **quantifies thermodynamic model-form
uncertainty**. The last directly addresses the standard reservation about
open-source thermo: rather than claiming fidelity parity, we *measure* how much a
result depends on the package choice.

**Rigour and honesty.** We are deliberate about the boundary between engineered
capability, method validated against a closed-form answer, and live-engine
demonstration. A capstone case study (two-stage compression with intercooling)
shows the agent's optimizer driving a real DWSIM flowsheet to an interior optimum
that matches the textbook closed-form value via three independent methods. We
also state, plainly, the two gaps that are fundamental to building on DWSIM
rather than replacing it (thermo fidelity; true native equation-oriented
optimization), and we present the system as *Aspen-level optimization methodology
over an open simulator*, not "Aspen-level simulation."

**Reproducibility.** The complete system, a content-hashed 25-task benchmark, 558
component tests, append-only agent transcripts, and a fully-wired
component-attribution ablation harness (with non-parametric statistics) are
released so that every reported result is reproducible.

This manuscript is original, not under consideration elsewhere, and all authors
have approved the submission. We have no conflicts of interest to declare. We
believe it will interest readers working at the intersection of AI, process
simulation, and optimization, and we thank you for considering it.

Sincerely,

Vishal Bhadauriya
M.Tech, Harcourt Butler Technical University (HBTU), Kanpur
*(corresponding author)*
