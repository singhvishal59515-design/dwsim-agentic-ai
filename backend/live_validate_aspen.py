"""Live-DWSIM validation of the Aspen-parity contributions that don't need the
LLM (trust-region EO, parallel pool, TAC). Builds a heater on the real engine
and exercises each, writing LIVE_ASPEN_VALIDATION.md."""
import os, time, json
from dwsim_bridge_v2 import DWSIMBridgeV2
from dwsim_native_optimizer import _read_object_property

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = {"name":"aspen_live_heater","compounds":["Water"],"property_package":"Peng-Robinson (PR)",
 "objects":[{"tag":"Feed","type":"MaterialStream"},{"tag":"H-101","type":"Heater"},{"tag":"Hot","type":"MaterialStream"}],
 "connections":[{"from_tag":"Feed","to_tag":"H-101"},{"from_tag":"H-101","to_tag":"Hot"}],
 "feed_specs":[{"tag":"Feed","temperature":25,"temperature_unit":"C","pressure":2,"pressure_unit":"bar","massflow":1.0,"massflow_unit":"kg/s","composition":{"Water":1.0}}],
 "unit_op_specs":[{"tag":"H-101","property_name":"outlet_temperature","value":90,"unit":"C"}]}
fv=lambda x:(float(x) if x is not None else float('nan'))
L=[]; w=lambda s='':L.append(s)
print("[live] init DWSIM + build heater…", flush=True)
b=DWSIMBridgeV2(); b.initialize(); b.build_flowsheet_atomic(SPEC)
path=os.path.join(HERE,"aspen_live_heater.dwxmz"); b.save_flowsheet(path)
w("# Live-DWSIM Validation of the Aspen-parity Contributions"); w()
w("Real DWSIM v9.0.5 engine, no LLM. Test flowsheet: Water heater (Feed 25 C / "
  "2 bar / 1 kg/s → H-101 → Hot), objective = heater duty (kW)."); w()

# 1. Trust-region EO on the live flowsheet
print("[live] trust-region EO…", flush=True)
from eo_optimizer import run_eo_trust_region
def evaluate(x):
    b.set_unit_op_property("H-101","outlet_temperature",float(x[0])); b.save_and_solve()
    return {"objective": fv(_read_object_property(b,"H-101","HeatDuty")), "constraint_values":[]}
t0=time.monotonic()
r=run_eo_trust_region(evaluate,[{"tag":"H-101","property":"outlet_temperature","unit":"C","lower":40,"upper":120}],minimize=True,x0=[90.0],max_iter=15)
w("## 1. Trust-region surrogate EO (live)")
w(f"- minimise duty → outlet T = **{list(r['design'].values()) if 'design' in r else list(r['x'].values())}** C, "
  f"duty = **{r['objective']:.2f} kW**, converged={r['converged']}, evals={r['n_evaluations']}, {time.monotonic()-t0:.0f}s")
w(f"- expected: drives to the 40 C lower bound (min duty); reached "
  f"{'✅' if abs(list(r['x'].values())[0]-40)<2 else '❌'}"); w()

# 2. Parallel evaluation on the live engine (speedup vs serial)
print("[live] parallel batch…", flush=True)
designs=[[t] for t in (40,50,60,70,80,90,100,110)]
# serial baseline in THIS process
t0=time.monotonic()
for d in designs:
    b.set_unit_op_property("H-101","outlet_temperature",float(d[0])); b.save_and_solve(); _read_object_property(b,"H-101","HeatDuty")
serial=time.monotonic()-t0
t0=time.monotonic()
pr=b.parallel_evaluate_designs(variables=[{"tag":"H-101","property":"outlet_temperature","unit":"C","lower":40,"upper":120}],
    observe_tag="H-101",observe_property="HeatDuty",designs=designs,n_workers=4)
par=time.monotonic()-t0
duties=[(round(d[0]),round(fv(rr.get('objective')),1)) for d,rr in zip(designs,pr.get('results',[]))]
w("## 2. Parallel flowsheet evaluation (live, 4 private CLRs)")
w(f"- evaluated {len(designs)} designs: serial **{serial:.1f}s**, parallel(4w) **{par:.1f}s** → "
  f"speedup **{serial/par:.2f}×**" if par>0 else "- parallel failed")
w(f"- duties (outletT C → kW): {duties}"); w()

# 3. TAC from live results
print("[live] TAC…", flush=True)
from tac_objective import total_annualized_cost
b.set_unit_op_property("H-101","outlet_temperature",90.0); b.save_and_solve()
duty=fv(_read_object_property(b,"H-101","HeatDuty"))
tac=total_annualized_cost([{"type":"heater","size":duty}],[{"kind":"heat","duty_kW":duty}])
w("## 3. TAC from live DWSIM results")
w(f"- at outlet 90 C, live duty = **{duty:.2f} kW** → TAC = **${tac['tac']:,.0f}/yr** "
  f"(annualised capex ${tac['annualized_capex']:,.0f} + opex ${tac['annual_opex']:,.0f})"); w()
w("## Note")
w("Infeasible-path SQP is not exercised here (needs a recycle flowsheet + the "
  "OT_Recycle single-pass hook); it remains validated on the analytic recycle.")
open(os.path.join(HERE,"LIVE_ASPEN_VALIDATION.md"),"w",encoding="utf-8").write("\n".join(L)+"\n")
print("[live] wrote LIVE_ASPEN_VALIDATION.md", flush=True)
