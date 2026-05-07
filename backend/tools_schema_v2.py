"""
tools_schema_v2.py
──────────────────
39 tool definitions in OpenAI function-calling JSON Schema format.
New tools vs v1 (13 -> 16 -> 22 -> 26 -> 28 -> 35 -> 39):
  check_convergence            — ACC-2: verify every stream converged after solve
  validate_feed_specs          — ACC-4: warn if T/P/flow missing before solve
  set_stream_composition       — ACC-1: set mole fractions on a feed stream
  get_property_package         — ACC-3: read thermodynamic model from flowsheet
  optimize_parameter           — ACC-5: SciPy bounded minimise/maximise
  get_column_properties        — v3: distillation column specs + results
  set_column_property          — v3: set column spec (reflux, stages, pressure)
  get_reactor_properties       — v3: all 5 reactor types (CSTR, PFR, Gibbs, etc.)
  set_reactor_property         — v3: set reactor parameters
  detect_simulation_mode       — v3: steady-state vs dynamic flowsheet detection
  get_plugin_info              — v3: Cantera / Reaktoro / Excel / FOSSEE custom UOs
  get_available_compounds      — v4: search DWSIM compound database
  get_available_property_packages — v4: list all thermodynamic models
  create_flowsheet             — v4: autonomous zero-to-one flowsheet generation
  generate_report              — v4: auto-generate academic PDF research report
  get_phase_results            — v5: phase-specific T/P/H/S/fractions (vapor/liquid/solid)
  get_energy_stream            — v5: read energy stream duty (W/kW/kJ/h)
  set_energy_stream            — v5: write energy stream duty (W)
  delete_object                — v5: remove stream or unit op from flowsheet
  disconnect_streams           — v5: sever unit-op ↔ stream connection
  setup_reaction               — v5: configure conversion/kinetic reactions on reactor
  set_column_specs             — v5: batch-set distillation column specs in one call
"""

DWSIM_TOOLS = [
    # ── flowsheet discovery & loading ─────────────────────────────────────────
    {
        "name": "find_flowsheets",
        "description": (
            "Search the local computer for DWSIM flowsheet files (.dwxmz, .dwxm). "
            "Call this FIRST when no flowsheet is loaded. "
            "Returns a list of real file paths — use one with load_flowsheet."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "load_flowsheet",
        "description": (
            "Load a DWSIM flowsheet file into memory and auto-solve it. "
            "Returns streams, unit operations, property package, and any feed warnings. "
            "Optionally provide an alias for multi-flowsheet use."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute file path to the .dwxmz or .dwxm flowsheet.",
                },
                "alias": {
                    "type": "string",
                    "description": "Optional short name (e.g. 'HE', 'reactor'). Defaults to filename.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "save_flowsheet",
        "description": "Save the currently active flowsheet to disk.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional save path. Defaults to original file.",
                }
            },
            "required": [],
        },
    },

    # ── multi-flowsheet ───────────────────────────────────────────────────────
    {
        "name": "list_loaded_flowsheets",
        "description": (
            "List all currently loaded flowsheets and which one is active."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "switch_flowsheet",
        "description": "Switch the active flowsheet to a previously loaded one by its alias.",
        "parameters": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "Alias of the flowsheet to switch to.",
                }
            },
            "required": ["alias"],
        },
    },

    # ── object enumeration ────────────────────────────────────────────────────
    {
        "name": "list_simulation_objects",
        "description": (
            "List every stream and unit operation in the loaded flowsheet "
            "with its tag, type, and category."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    # ── property reading ──────────────────────────────────────────────────────
    {
        "name": "get_stream_properties",
        "description": (
            "Read the full thermodynamic state of a material stream: "
            "T (K, °C), P (Pa, kPa, bar), molar/mass flow, "
            "vapour fraction, enthalpy, and mole fractions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the material stream."}
            },
            "required": ["tag"],
        },
    },
    {
        "name": "get_object_properties",
        "description": (
            "Inspect any simulation object (stream or unit operation). "
            "For unit operations returns a clean engineering summary "
            "(heat exchanger: area, U, duty, LMTD; pump: ΔP, efficiency, power). "
            "For streams returns full thermodynamic properties."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the simulation object."}
            },
            "required": ["tag"],
        },
    },

    # ── property writing ──────────────────────────────────────────────────────
    {
        "name": "set_stream_property",
        "description": (
            "Set a feed specification on a material stream. "
            "After setting, call run_simulation to recalculate. "
            "Supported: temperature, pressure, molar_flow, mass_flow, vapor_fraction. "
            "Units: temperature → K or C; pressure → Pa, bar, kPa, atm, psi; "
            "molar_flow → mol/s, mol/h, kmol/h; mass_flow → kg/s, kg/h, t/h."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the stream to modify."},
                "property_name": {
                    "type": "string",
                    "enum": ["temperature", "pressure", "molar_flow",
                             "mass_flow", "vapor_fraction"],
                    "description": "Which property to set.",
                },
                "value": {
                    "type": "number",
                    "description": (
                        "Numeric value in the given unit. "
                        "Safe ranges: temperature 0–2500 K (or -273–2227 °C); "
                        "pressure 0–1.5e8 Pa (0–1500 bar); "
                        "flows ≥ 0; vapor_fraction 0–1."
                    ),
                },
                "unit":  {"type": "string",
                          "description": "Unit string, e.g. 'C', 'bar', 'kg/h'. Leave empty for SI default."},
            },
            "required": ["tag", "property_name", "value"],
        },
    },
    {
        "name": "set_unit_op_property",
        "description": (
            "Set a parameter on a unit operation. "
            "Use get_object_properties first to see available parameter names. "
            "Examples: Efficiency, Conversion, DeltaP, OutletTemperature, "
            "Area, OverallCoefficient, RefluxRatio. "
            "Efficiency and Conversion must be in [0, 1]. "
            "OutletTemperature range: 0–2500 K (or equivalent in °C/°F)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag":           {"type": "string", "description": "Tag of the unit operation."},
                "property_name": {"type": "string", "description": "Attribute name (case-insensitive)."},
                "value":         {"type": "string",
                                  "description": (
                                      "Value to set. Efficiency/Conversion: 0–1 (fraction). "
                                      "OutletTemperature: in K by default (or specify unit). "
                                      "DeltaP: Pa by default."
                                  )},
                "unit":          {"type": "string",
                                  "description": "Optional unit for auto-conversion, e.g. 'C', 'bar', 'kW'."},
            },
            "required": ["tag", "property_name", "value"],
        },
    },

    # ── ACC-1: set composition ────────────────────────────────────────────────
    {
        "name": "set_stream_composition",
        "description": (
            "ACC-1: Set mole fractions on a feed stream. "
            "The compositions dict maps component name → mole fraction. "
            "Values must sum to 1.0. After setting, call run_simulation. "
            "Example: {'Methanol': 0.8, 'Water': 0.2}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Tag of the material stream to modify.",
                },
                "compositions": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                    "description": "Dict of component_name → mole_fraction. Must sum to 1.0.",
                },
            },
            "required": ["tag", "compositions"],
        },
    },

    # ── simulation execution ──────────────────────────────────────────────────
    {
        "name": "run_simulation",
        "description": (
            "Solve the flowsheet with current parameter values. "
            "Always call after changing any stream specs or unit-op parameters. "
            "Returns convergence_errors and a per-stream convergence_check."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_simulation_results",
        "description": (
            "Retrieve the post-solve thermodynamic state of every material stream. "
            "Call after run_simulation."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_convergence",
        "description": (
            "ACC-2: Verify that every material stream converged after the last solve. "
            "Returns lists of converged and not_converged stream tags, plus "
            "missing specs for each unconverged stream. "
            "Call after run_simulation to confirm the solution is valid."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "validate_feed_specs",
        "description": (
            "ACC-4: Check all feed streams for missing temperature, pressure, "
            "or flow specifications before running a simulation. "
            "Returns a warnings list — each entry describes a missing spec. "
            "Call before run_simulation when you suspect incomplete feed data."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    # ── ACC-3: property package ───────────────────────────────────────────────
    {
        "name": "get_property_package",
        "description": (
            "ACC-3: Read the thermodynamic property package (equation of state) "
            "used by the flowsheet, e.g. Peng-Robinson, SRK, NRTL, Steam Tables. "
            "Call this to understand accuracy/applicability of the simulation."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    # ── parametric study ──────────────────────────────────────────────────────
    {
        "name": "parametric_study",
        "description": (
            "Run the simulation at multiple values of one parameter and record "
            "how an observed stream property changes. "
            "Returns a results table."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vary_tag":         {"type": "string", "description": "Tag of the stream/unit-op to vary."},
                "vary_property":    {"type": "string", "description": "Property to vary, e.g. 'mass_flow'."},
                "vary_unit":        {"type": "string", "description": "Unit, e.g. 'kg/h'."},
                "values":           {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "List of values to simulate at.",
                },
                "observe_tag":      {"type": "string", "description": "Tag of the stream to observe."},
                "observe_property": {
                    "type": "string",
                    "description": "Property key to read, e.g. 'temperature_C', 'mass_flow_kgh'.",
                },
            },
            "required": ["vary_tag", "vary_property", "vary_unit",
                         "values", "observe_tag", "observe_property"],
        },
    },

    # ── v3: distillation column ───────────────────────────────────────────────
    {
        "name": "get_column_properties",
        "description": (
            "v3: Read all specifications and results for a distillation column "
            "(ShortcutColumn, DistillationColumn, AbsorptionColumn, "
            "RefluxedAbsorber, ReboiledAbsorber). "
            "Returns: number of stages, reflux ratio, condenser/reboiler duties, "
            "distillate rate, bottoms rate, condenser type, pressure profile, "
            "and convergence status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the column unit operation."}
            },
            "required": ["tag"],
        },
    },
    {
        "name": "set_column_property",
        "description": (
            "v3: Set a specification on a distillation column before re-solving. "
            "Common properties: NumberOfStages, RefluxRatio, CondenserPressure, "
            "ReboilerPressure, DistillateFlowRate, BottomsFlowRate, "
            "CondenserType (0=Total, 1=Partial), FeedStage. "
            "Always call run_simulation after."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag":           {"type": "string", "description": "Tag of the column unit operation."},
                "property_name": {"type": "string", "description": "Property name to set."},
                "value":         {"type": "string", "description": "Value to set (numeric or string)."},
            },
            "required": ["tag", "property_name", "value"],
        },
    },

    # ── v3: reactors ──────────────────────────────────────────────────────────
    {
        "name": "get_reactor_properties",
        "description": (
            "v3: Read specifications and results for any reactor type: "
            "CSTR (ContinuousStirredTankReactor), "
            "PFR (PlugFlowReactor), "
            "Gibbs (GibbsReactor / equilibrium), "
            "Conversion (ConversionReactor), "
            "EquilibriumReactor. "
            "Returns: volume/length, temperature mode, reactions list, "
            "conversion, heat duty, pressure drop, outlet conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the reactor unit operation."}
            },
            "required": ["tag"],
        },
    },
    {
        "name": "set_reactor_property",
        "description": (
            "v3: Set a parameter on any reactor before re-solving. "
            "Common properties: Volume (m³), Length (m), Diameter (m), "
            "OutletTemperature (K), Pressure (Pa), "
            "Conversion (0-1, for ConversionReactor), "
            "ResidenceTime (s), HeatDuty (W), OperationMode. "
            "Always call run_simulation after."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag":           {"type": "string", "description": "Tag of the reactor unit operation."},
                "property_name": {"type": "string", "description": "Property name to set."},
                "value":         {"type": "string", "description": "Value to set."},
            },
            "required": ["tag", "property_name", "value"],
        },
    },

    # ── v3: simulation mode detection ─────────────────────────────────────────
    {
        "name": "detect_simulation_mode",
        "description": (
            "v3: Detect whether the loaded flowsheet is steady-state or dynamic, "
            "and report the dynamic integrator settings if applicable "
            "(integrator type, time step, end time, current time). "
            "Also reports whether DAE/ODE solvers are configured."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    # ── v3: plugin / custom unit ops ──────────────────────────────────────────
    {
        "name": "get_plugin_info",
        "description": (
            "v3: Inspect any plugin or custom unit operation: "
            "Cantera (reacting flow), Reaktoro (geochemistry), "
            "Excel UO (spreadsheet-backed), Python Script UO, "
            "FOSSEE custom blocks. "
            "Returns the plugin type, configuration parameters, "
            "script/file path, and last calculation status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag of the plugin/custom unit operation."}
            },
            "required": ["tag"],
        },
    },

    # ── ACC-5: optimize parameter ─────────────────────────────────────────────
    {
        "name": "optimize_parameter",
        "description": (
            "ACC-5: Use SciPy bounded scalar optimisation to find the value of a "
            "stream or unit-op parameter that minimises (or maximises) an observed "
            "stream property. "
            "Example: find the water flow that minimises outlet temperature."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vary_tag":         {"type": "string", "description": "Tag of the stream/unit-op to vary."},
                "vary_property":    {"type": "string", "description": "Property to vary, e.g. 'mass_flow'."},
                "vary_unit":        {"type": "string", "description": "Unit, e.g. 'kg/h'."},
                "lower_bound":      {"type": "number", "description": "Lower bound of the search range."},
                "upper_bound":      {"type": "number", "description": "Upper bound of the search range."},
                "observe_tag":      {"type": "string", "description": "Tag of the stream to observe."},
                "observe_property": {"type": "string", "description": "Property to optimise, e.g. 'temperature_C'."},
                "minimize":         {
                    "type": "boolean",
                    "description": "True to minimise (default), False to maximise.",
                    "default": True,
                },
                "tolerance":        {"type": "number", "description": "Convergence tolerance (default 1e-4)."},
                "max_iterations":   {"type": "integer", "description": "Max solver iterations (default 50)."},
            },
            "required": ["vary_tag", "vary_property", "vary_unit",
                         "lower_bound", "upper_bound",
                         "observe_tag", "observe_property"],
        },
    },

    # ── Bayesian Optimisation ─────────────────────────────────────────────────
    {
        "name": "bayesian_optimize",
        "description": (
            "Bayesian Optimisation — finds optimal DWSIM operating conditions using a "
            "Gaussian Process surrogate model with Expected Improvement acquisition. "
            "BEST CHOICE when: (1) simulations are expensive (>10s each), "
            "(2) you have 1–5 decision variables, "
            "(3) you need results quickly (10–25 evaluations vs 100+ for differential evolution). "
            "Examples: find reflux ratio + feed temperature that minimise reboiler duty; "
            "find compressor pressure + efficiency that maximise product flow. "
            "Use optimize_parameter for 1D problems; optimize_multivar for >5 variables."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "array",
                    "description": (
                        "List of decision variables. Each element is an object with: "
                        "tag (stream or unit-op tag), property (e.g. 'temperature', 'mass_flow'), "
                        "unit (e.g. 'C', 'kg/h'), lower_bound (float), upper_bound (float)."
                    ),
                    "items": {
                        "type": "object",
                        "required": ["tag", "property", "lower_bound", "upper_bound"],
                        "properties": {
                            "tag":         {"type": "string", "description": "Stream or unit-op tag."},
                            "property":    {"type": "string", "description": "Property to vary."},
                            "unit":        {"type": "string", "description": "Unit string."},
                            "lower_bound": {"type": "number", "description": "Lower bound of search range."},
                            "upper_bound": {"type": "number", "description": "Upper bound of search range."},
                        },
                    },
                },
                "observe_tag":      {"type": "string", "description": "Tag of the stream to observe."},
                "observe_property": {"type": "string", "description": "Property to optimise, e.g. 'temperature_C'."},
                "minimize": {
                    "type": "boolean",
                    "description": "True to minimise (default), False to maximise.",
                    "default": True,
                },
                "n_initial": {
                    "type": "integer",
                    "description": "Latin-Hypercube exploration evaluations before BO starts (default 5).",
                    "default": 5,
                },
                "max_iter": {
                    "type": "integer",
                    "description": "Bayesian optimisation iterations after initial phase (default 20). Total = n_initial + max_iter.",
                    "default": 20,
                },
                "tolerance": {
                    "type": "number",
                    "description": "Early-stop tolerance — stops if improvement < tolerance for 5 consecutive iters (default 1e-4).",
                    "default": 1e-4,
                },
            },
            "required": ["variables", "observe_tag", "observe_property"],
        },
    },

    # ── v4: Autonomous Flowsheet Generation ──────────────────────────────────


    {
        "name": "get_available_compounds",
        "description": (
            "Search the DWSIM compound database (1488 compounds). "
            "Use this before create_flowsheet to verify compound names. "
            "Returns matching compound names that can be used in 'compounds' list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Search string, e.g. 'methanol'. Leave empty to list first 200.",
                },
            },
            "required": [],
        },
    },

    {
        "name": "get_available_property_packages",
        "description": (
            "List all thermodynamic property packages available in DWSIM. "
            "Use this to get exact PP names for create_flowsheet's property_package field. "
            "Common ones: 'Peng-Robinson (PR)', 'NRTL', 'SRK', \"Raoult's Law\"."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    # create_flowsheet removed — AI now builds flowsheets step-by-step using
    # new_flowsheet → add_object → connect_streams → set_stream_property /
    # set_unit_op_property → save_and_solve → get_simulation_results
    {
        "name": "create_flowsheet",
        "description": (
            "[DISABLED — do NOT call this tool. "
            "Use new_flowsheet + add_object + connect_streams + "
            "set_stream_property + set_unit_op_property + save_and_solve instead.]"
        ),
        "parameters": {
            "type": "object",
            "required": ["topology"],
            "properties": {
                "topology": {
                    "type": "object",
                    "description": "Flowsheet topology specification",
                    "required": ["compounds", "property_package", "streams", "connections"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Flowsheet name used as filename, e.g. 'methanol_water_sep'",
                        },
                        "save_path": {
                            "type": "string",
                            "description": "Absolute save path, e.g. C:/Users/hp/Documents/sim.dwxmz",
                        },
                        "property_package": {
                            "type": "string",
                            "description": (
                                "Thermodynamic model key. Examples: "
                                "'Peng-Robinson (PR)', 'Soave-Redlich-Kwong (SRK)', "
                                "'NRTL', 'UNIFAC', \"Raoult's Law\", "
                                "'Steam Tables (IAPWS-IF97)', 'CoolProp', 'Wilson'"
                            ),
                        },
                        "compounds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Compound names from DWSIM DB, e.g. ['Water', 'Methanol']",
                        },
                        "streams": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["tag"],
                                "properties": {
                                    "tag":          {"type": "string"},
                                    "type":         {"type": "string",
                                                     "description": "MaterialStream (default) or EnergyStream"},
                                    "T":            {"type": "number"},
                                    "T_unit":       {"type": "string", "description": "K or C"},
                                    "P":            {"type": "number"},
                                    "P_unit":       {"type": "string", "description": "Pa, bar, kPa, atm"},
                                    "molar_flow":   {"type": "number"},
                                    "flow_unit":    {"type": "string", "description": "mol/s, mol/h, kmol/h"},
                                    "mass_flow":    {"type": "number", "description": "kg/s"},
                                    "vapor_fraction": {"type": "number"},
                                    "compositions": {
                                        "type": "object",
                                        "description": "Mole fractions summing to 1.0",
                                        "additionalProperties": {"type": "number"},
                                    },
                                },
                            },
                        },
                        "unit_ops": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["tag", "type"],
                                "properties": {
                                    "tag":              {"type": "string"},
                                    "type":             {
                                        "type": "string",
                                        "description": (
                                            "Exact type names: Heater, Cooler, HeatExchanger, "
                                            "Pump, Compressor, Expander, Valve, "
                                            "Mixer (stream combiner), Splitter (stream divider), "
                                            "Separator (flash drum / two-phase vessel — also accepts "
                                            "'flash', 'flash drum', 'flash tank'), "
                                            "DistillationColumn, AbsorptionColumn, ShortcutColumn, "
                                            "CSTR, PFR, GibbsReactor, ConversionReactor, "
                                            "EquilibriumReactor, Pipe, CompoundSeparator"
                                        ),
                                    },
                                    "outlet_T":         {"type": "number", "description": "Outlet T in K (Heater/Cooler only)"},
                                    "pressure_drop":    {"type": "number", "description": "dP in Pa"},
                                    "duty":             {"type": "number", "description": "Heat duty in W"},
                                    "efficiency":       {"type": "number", "description": "0-1"},
                                    "reflux_ratio":     {"type": "number"},
                                    "stages":           {"type": "integer"},
                                    "number_of_stages": {"type": "integer"},
                                    "volume":           {"type": "number", "description": "m3"},
                                    "conversion":       {"type": "number", "description": "0-1"},
                                },
                            },
                        },
                        "connections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["from", "to"],
                                "properties": {
                                    "from":      {"type": "string"},
                                    "to":        {"type": "string"},
                                    "from_port": {"type": "integer",
                                                  "description": "Output port index (0=first outlet)"},
                                    "to_port":   {"type": "integer",
                                                  "description": "Input port index (0=first inlet)"},
                                },
                            },
                        },
                        "run_simulation": {
                            "type": "boolean",
                            "description": "Whether to solve after building (default true)",
                        },
                    },
                },
            },
        },
    },

    {
        "name": "list_flowsheet_templates",
        "description": (
            "List the curated library of starter flowsheet topologies. "
            "Each entry has a name, category (separation, reaction, heat, "
            "pressure, recycle, renewables, simple), and a one-line description. "
            "Use this when the user asks for a flowsheet type but hasn't given "
            "a full spec — pick a template, then call create_from_template with "
            "overrides to customise compounds / conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },

    # ── AI step-by-step building tools ───────────────────────────────────────
    {
        "name": "new_flowsheet",
        "description": (
            "Create a blank DWSIM flowsheet with the specified compounds and "
            "thermodynamic property package. This is STEP 1 when building any "
            "new flowsheet. After this call, use add_object to add streams and "
            "unit operations one by one."
        ),
        "parameters": {
            "type": "object",
            "required": ["name", "compounds", "property_package"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Flowsheet name, used as filename. e.g. 'water_heater'",
                },
                "compounds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Compound names from DWSIM database. e.g. ['Water', 'Methanol']",
                },
                "property_package": {
                    "type": "string",
                    "description": (
                        "Thermodynamic model. Examples: "
                        "'Steam Tables (IAPWS-IF97)' for water/steam, "
                        "'Peng-Robinson (PR)' for hydrocarbons/gas, "
                        "'NRTL' for polar liquid mixtures, "
                        "'Soave-Redlich-Kwong (SRK)' for gas processing, "
                        "'CoolProp' for refrigerants."
                    ),
                },
                "save_path": {
                    "type": "string",
                    "description": "Optional absolute save path, e.g. C:/Users/hp/Documents/sim.dwxmz",
                },
            },
        },
    },
    {
        "name": "add_object",
        "description": (
            "Add one stream or unit operation to the active flowsheet by tag and type. "
            "STEP 2 — call once for each object in the flowsheet after new_flowsheet. "
            "After adding all objects, use connect_streams to wire them together."
        ),
        "parameters": {
            "type": "object",
            "required": ["tag", "type"],
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Display name for this object, e.g. 'Feed', 'H-101', 'Product'",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Object type. Streams: MaterialStream, EnergyStream. "
                        "Unit ops: Heater, Cooler, HeatExchanger, Pump, Compressor, "
                        "Expander, Valve, Mixer, Splitter, Separator, "
                        "DistillationColumn, AbsorptionColumn, ShortcutColumn, "
                        "CSTR, PFR, GibbsReactor, ConversionReactor, EquilibriumReactor, Pipe"
                    ),
                },
            },
        },
    },
    {
        "name": "save_and_solve",
        "description": (
            "Save the active flowsheet to disk and run the DWSIM solver. "
            "Call this as the FINAL step after all objects are added, connected, "
            "and properties are set. Returns converged status and stream results."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },

    {
        "name": "generate_report",
        "description": (
            "Generate a formatted academic PDF research report from a parametric study. "
            "Produces: 3 engineering-quality plots (trend, bar chart, sensitivity), "
            "a structured PDF with Title page, Abstract, Setup, Methodology, Results, "
            "Statistical Summary, Conclusion, and a raw data appendix table. "
            "WORKFLOW: "
            "1. Run parametric_study to get study_data. "
            "2. Analyse the data thoroughly. "
            "3. Draft ALL SIX report_text sections: "
            "abstract, introduction, methodology, results, discussion, conclusion. "
            "4. Call generate_report with study_data + all six drafted sections. "
            "5. Report the pdf_path and key statistics to the user."
        ),
        "parameters": {
            "type": "object",
            "required": ["title", "study_data", "report_text"],
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Report title, e.g. "
                        "'Effect of Feed Temperature on Methanol Outlet Concentration'"
                    ),
                },
                "study_data": {
                    "type": "object",
                    "description": (
                        "The complete dict returned by parametric_study. "
                        "Must contain 'table' or 'results' key with the data points."
                    ),
                },
                "report_text": {
                    "type": "object",
                    "description": (
                        "LLM-drafted text sections for the academic report. "
                        "Write all six sections BEFORE calling this tool. "
                        "Each must be a complete, fluent English paragraph — "
                        "never leave a section as a placeholder."
                    ),
                    "required": ["abstract", "methodology", "results"],
                    "properties": {
                        "abstract": {
                            "type": "string",
                            "description": (
                                "150-250 words. Context, objective, method, "
                                "key quantitative findings, and significance."
                            ),
                        },
                        "introduction": {
                            "type": "string",
                            "description": (
                                "150-300 words. Background on the process/unit "
                                "operation, industrial relevance, motivation for "
                                "the parametric study, and scope of analysis."
                            ),
                        },
                        "methodology": {
                            "type": "string",
                            "description": (
                                "200-400 words. DWSIM flowsheet description, "
                                "thermodynamic model used, feed specifications, "
                                "parameter ranges studied, and simulation procedure."
                            ),
                        },
                        "results": {
                            "type": "string",
                            "description": (
                                "200-350 words. Objective description of what "
                                "the data shows: trend direction, magnitude of "
                                "change, location of optima, and notable inflection "
                                "points. Reference figure numbers (Figure 1, 2, 3)."
                            ),
                        },
                        "discussion": {
                            "type": "string",
                            "description": (
                                "200-350 words. Explain WHY the observed trends "
                                "occur (thermodynamics / transport phenomena), "
                                "compare with theory or literature expectations, "
                                "practical implications, and limitations of the model."
                            ),
                        },
                        "conclusion": {
                            "type": "string",
                            "description": (
                                "100-200 words. Summarise key findings with "
                                "specific numbers, engineering recommendations, "
                                "and suggestions for future work."
                            ),
                        },
                    },
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Directory to save the PDF and PNG plots. "
                        "Defaults to ~/Documents/dwsim_reports/"
                    ),
                },
                "output_pdf": {
                    "type": "string",
                    "description": "Override full absolute path for the PDF file.",
                },
            },
        },
    },

    # ── v5: phase results ─────────────────────────────────────────────────────
    {
        "name": "get_phase_results",
        "description": (
            "Read phase-specific thermodynamic properties (T, P, H, S, density, "
            "molar/mass flow, mole fractions) for a single phase of a material stream. "
            "Use this when you need to know the properties of the vapor phase, liquid "
            "phase, or solid phase separately — for example, after a flash drum or "
            "distillation column to check vapor-liquid split. "
            "phase values: 'vapor', 'liquid' (= liquid1), 'liquid2', 'solid', 'overall'."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the material stream to read.",
                },
                "phase": {
                    "type": "string",
                    "description": (
                        "Phase to read: 'vapor', 'liquid', 'liquid1', 'liquid2', "
                        "'solid', 'overall'. Defaults to 'vapor'."
                    ),
                    "default": "vapor",
                },
            },
        },
    },

    # ── v6: transport / physical properties ──────────────────────────────────
    {
        "name": "get_transport_properties",
        "description": (
            "Read transport/physical properties (density, viscosity, Cp, Cv, "
            "thermal conductivity, surface tension, molecular weight, "
            "compressibility Z, volumetric flow) for a stream phase. "
            "Use for heat-transfer sizing, pressure-drop calculations, or to "
            "inspect phase-dependent fluid properties."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the material stream.",
                },
                "phase": {
                    "type": "string",
                    "description": (
                        "Phase: 'overall', 'vapor', 'liquid', 'liquid1', "
                        "'liquid2', 'solid'. Defaults to 'overall'."
                    ),
                    "default": "overall",
                },
            },
        },
    },
    {
        "name": "calculate_phase_envelope",
        "description": (
            "Compute a phase envelope using the stream's property package.\n"
            "envelope_type values:\n"
            "  'PT'  — bubble + dew curves over the full P–T plane\n"
            "  'Txy' — binary T vs x,y at fixed P (isobaric)\n"
            "  'Pxy' — binary P vs x,y at fixed T (isothermal)\n"
            "For Txy/Pxy the first two SelectedCompounds of the flowsheet "
            "define the binary pair."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": (
                        "Tag of a material stream whose composition / property "
                        "package defines the envelope."
                    ),
                },
                "envelope_type": {
                    "type": "string",
                    "description": "'PT', 'Txy', or 'Pxy'.",
                    "default": "PT",
                },
                "max_points": {
                    "type": "integer",
                    "description": "Max points per curve for PT mode.",
                    "default": 50,
                },
                "quality": {
                    "type": "number",
                    "description": "PT mode quality line value in [0,1].",
                    "default": 0.0,
                },
                "fixed_P_Pa": {
                    "type": "number",
                    "description": "Pressure (Pa) for Txy mode.",
                    "default": 101325.0,
                },
                "fixed_T_K": {
                    "type": "number",
                    "description": "Temperature (K) for Pxy mode.",
                    "default": 298.15,
                },
                "step_count": {
                    "type": "integer",
                    "description": "Resolution along x for Txy/Pxy.",
                    "default": 40,
                },
            },
        },
    },
    {
        "name": "get_binary_interaction_parameters",
        "description": (
            "Read binary interaction parameters from the active property "
            "package. For PR/SRK returns the kij matrix. For NRTL/UNIQUAC "
            "returns A12/A21/B12/B21/C12/C21 (and alpha12 for NRTL). If "
            "compound_1 and compound_2 are given, returns just that pair."
        ),
        "parameters": {
            "type": "object",
            "required": [],
            "properties": {
                "compound_1": {"type": "string"},
                "compound_2": {"type": "string"},
            },
        },
    },
    {
        "name": "set_binary_interaction_parameters",
        "description": (
            "Write binary interaction parameters on the active property "
            "package. Cubic EOS: pass kij. NRTL/UNIQUAC: pass any of A12, "
            "A21, B12, B21, C12, C21 (and alpha12 for NRTL). Parameters not "
            "mentioned are left untouched."
        ),
        "parameters": {
            "type": "object",
            "required": ["compound_1", "compound_2"],
            "properties": {
                "compound_1": {"type": "string"},
                "compound_2": {"type": "string"},
                "kij":     {"type": "number"},
                "A12":     {"type": "number"},
                "A21":     {"type": "number"},
                "B12":     {"type": "number"},
                "B21":     {"type": "number"},
                "C12":     {"type": "number"},
                "C21":     {"type": "number"},
                "alpha12": {"type": "number"},
            },
        },
    },
    {
        "name": "configure_heat_exchanger",
        "description": (
            "Configure a HeatExchanger unit op's calculation mode and design "
            "parameters (area, U, outlet temperatures, pressure drops, flow "
            "direction). Use to switch between sizing ('CalcArea'), rating "
            "('CalcBothTemp_UA'), and energy-balance ('CalcBothTemp') modes."
        ),
        "parameters": {
            "type": "object",
            "required": ["hx_tag"],
            "properties": {
                "hx_tag": {"type": "string"},
                "mode": {
                    "type": "string",
                    "description": (
                        "'CalcTempHotOut' | 'CalcTempColdOut' | "
                        "'CalcBothTemp' | 'CalcBothTemp_UA' | 'CalcArea' | "
                        "'PinchPoint' | 'ThermalEfficiency' | "
                        "'OutletVaporFraction1' | 'OutletVaporFraction2' | "
                        "'ShellandTube_Rating' | 'ShellandTube_CalcFoulingFactor'"
                    ),
                },
                "area_m2":         {"type": "number"},
                "overall_U_W_m2K": {"type": "number"},
                "hot_outlet_T_K":  {"type": "number"},
                "cold_outlet_T_K": {"type": "number"},
                "hot_dp_Pa":       {"type": "number"},
                "cold_dp_Pa":      {"type": "number"},
                "duty_W":          {"type": "number"},
                "flow_direction": {
                    "type": "string",
                    "description": "'counter' or 'cocurrent'",
                },
                "lmtd_correction_F": {"type": "number"},
                "defined_temperature": {
                    "type": "string",
                    "description": "'hot' or 'cold' (for CalcBothTemp mode)",
                },
            },
        },
    },
    {
        "name": "set_stream_flash_spec",
        "description": (
            "Set the flash calculation mode (SpecType) on a material stream. "
            "Use to switch a feed from TP (default) to PH/PS/PVF/TVF so DWSIM "
            "back-solves the missing variable on run_simulation. "
            "Valid spec: 'TP','PH','PS','PVF','TVF'."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag", "spec"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the material stream.",
                },
                "spec": {
                    "type": "string",
                    "description": (
                        "Flash spec: 'TP' (default), 'PH', 'PS', 'PVF', 'TVF'."
                    ),
                },
            },
        },
    },

    # ── v5: energy streams ────────────────────────────────────────────────────
    {
        "name": "get_energy_stream",
        "description": (
            "Read the duty (heat/power) of an energy stream in W, kW, kJ/h, and kcal/h. "
            "Use after run_simulation to check condenser/reboiler duties, heater/cooler "
            "loads, compressor power, etc."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the energy stream (e.g. 'QR', 'QC', 'E-01').",
                },
            },
        },
    },
    {
        "name": "set_energy_stream",
        "description": (
            "Set the duty of an energy stream (value in Watts). "
            "Use this to specify a fixed heat input/output before run_simulation."
        ),
        "parameters": {
            "type": "object",
            "required": ["stream_tag", "duty_W"],
            "properties": {
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the energy stream.",
                },
                "duty_W": {
                    "type": "number",
                    "description": (
                        "Duty in Watts. Positive = heat added, negative = heat removed. "
                        "Convert: 1 kW = 1000 W, 1 kcal/h = 1.163 W."
                    ),
                },
            },
        },
    },

    # ── v5: delete / disconnect ───────────────────────────────────────────────
    {
        "name": "delete_object",
        "description": (
            "Remove a stream or unit operation from the active flowsheet. "
            "Use with caution — this cannot be undone without reloading. "
            "Prefer disconnect_streams if you only want to sever a connection."
        ),
        "parameters": {
            "type": "object",
            "required": ["tag"],
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Tag of the stream or unit op to delete.",
                },
            },
        },
    },
    {
        "name": "disconnect_streams",
        "description": (
            "Sever the connection between a unit operation and a stream without "
            "deleting either object. Use when you need to rewire the flowsheet."
        ),
        "parameters": {
            "type": "object",
            "required": ["uo_tag", "stream_tag"],
            "properties": {
                "uo_tag": {
                    "type": "string",
                    "description": "Tag of the unit operation.",
                },
                "stream_tag": {
                    "type": "string",
                    "description": "Tag of the stream to disconnect from the unit op.",
                },
            },
        },
    },
    {
        "name": "connect_streams",
        "description": (
            "Wire two flowsheet objects together by tag. The 'from' object's output "
            "port (from_port, default 0) connects to the 'to' object's input port "
            "(to_port, default 0). Use this to build the material/energy graph after "
            "add_stream / add_unit_op. Returns a verified flag confirming the "
            "connection is attached on both sides."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_tag", "to_tag"],
            "properties": {
                "from_tag": {
                    "type": "string",
                    "description": "Tag of the source object (stream or unit op output).",
                },
                "to_tag": {
                    "type": "string",
                    "description": "Tag of the destination object (stream or unit op input).",
                },
                "from_port": {
                    "type": "integer",
                    "default": 0,
                    "description": "Output port index on the source object (usually 0).",
                },
                "to_port": {
                    "type": "integer",
                    "default": 0,
                    "description": "Input port index on the destination object (usually 0).",
                },
            },
        },
    },
    {
        "name": "validate_topology",
        "description": (
            "Graph-level sanity check on the active flowsheet. Returns a list of "
            "dangling streams (streams with no source or sink) and unit operations "
            "with unconnected required ports. Call before run_simulation to catch "
            "wiring mistakes early."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },

    # ── v5: reaction setup ────────────────────────────────────────────────────
    {
        "name": "setup_reaction",
        "description": (
            "Configure reactions on a conversion or equilibrium reactor. "
            "Supports conversion reactions (specify fractional conversion and base compound) "
            "and kinetic/equilibrium reactions (specify stoichiometry). "
            "Always call run_simulation after setup."
        ),
        "parameters": {
            "type": "object",
            "required": ["reactor_tag", "reactions"],
            "properties": {
                "reactor_tag": {
                    "type": "string",
                    "description": "Tag of the reactor unit operation.",
                },
                "reactions": {
                    "type": "array",
                    "description": "List of reaction specifications.",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Reaction label, e.g. 'R1: A → B'.",
                            },
                            "type": {
                                "type": "string",
                                "description": "'conversion', 'kinetic', or 'equilibrium'.",
                            },
                            "base_compound": {
                                "type": "string",
                                "description": "Limiting reactant for conversion reactions.",
                            },
                            "conversion": {
                                "type": "number",
                                "description": "Fractional conversion 0.0 – 1.0.",
                            },
                            "stoichiometry": {
                                "type": "object",
                                "description": (
                                    "Stoichiometric coefficients: negative for reactants, "
                                    "positive for products. E.g. {'A': -1, 'B': 1}."
                                ),
                                "additionalProperties": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
    },

    # ── v5: column batch spec ─────────────────────────────────────────────────
    {
        "name": "set_column_specs",
        "description": (
            "Batch-set multiple distillation column specifications in one call. "
            "More convenient than calling set_column_property repeatedly. "
            "Only provide the parameters you want to change; omit the rest. "
            "Call run_simulation after to apply changes."
        ),
        "parameters": {
            "type": "object",
            "required": ["column_tag"],
            "properties": {
                "column_tag": {
                    "type": "string",
                    "description": "Tag of the distillation/absorption column.",
                },
                "n_stages": {
                    "type": "integer",
                    "description": "Total number of theoretical stages.",
                },
                "reflux_ratio": {
                    "type": "number",
                    "description": "Reflux ratio L/D.",
                },
                "feed_stage": {
                    "type": "integer",
                    "description": "Feed stage number (1-indexed from top).",
                },
                "condenser_type": {
                    "type": "string",
                    "description": "'Total', 'Partial', or 'None'.",
                },
                "condenser_duty_W": {
                    "type": "number",
                    "description": "Fixed condenser duty in Watts (negative = cooling).",
                },
                "reboiler_duty_W": {
                    "type": "number",
                    "description": "Fixed reboiler duty in Watts.",
                },
                "distillate_rate_mol_s": {
                    "type": "number",
                    "description": "Distillate molar flow in mol/s.",
                },
                "bottoms_rate_mol_s": {
                    "type": "number",
                    "description": "Bottoms molar flow in mol/s.",
                },
                "condenser_pressure_Pa": {
                    "type": "number",
                    "description": "Condenser pressure in Pa.",
                },
                "reboiler_pressure_Pa": {
                    "type": "number",
                    "description": "Reboiler pressure in Pa.",
                },
            },
        },
    },

    # ── RAG Knowledge Base ────────────────────────────────────────────────────
    {
        "name": "search_knowledge",
        "description": (
            "Search the chemical engineering knowledge base for thermodynamic "
            "principles, equipment design heuristics, property package selection "
            "guidance, reactor theory, separation design, and troubleshooting tips. "
            "Call this BEFORE making design decisions or answering conceptual questions. "
            "Returns ranked chunks with source citations from Perry's, Smith & Van Ness, "
            "Turton, Fogler, and DWSIM documentation."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language search query. Examples: "
                        "'which property package for ethanol water', "
                        "'heat exchanger LMTD design', "
                        "'distillation column convergence issues', "
                        "'CSTR vs PFR reactor selection'."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 4, max: 8).",
                },
            },
        },
    },

    # ── Persistent memory (goals, constraints, history) ──────────────────────
    {
        "name": "remember_goal",
        "description": (
            "Persist a user-stated design goal so it's recalled automatically in "
            "future sessions. Use when the user says things like 'our goal is max "
            "product purity' or 'we want minimum utility cost'."
        ),
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string",
                         "description": "The design goal in one concise sentence."},
            },
        },
    },
    {
        "name": "remember_constraint",
        "description": (
            "Persist a plant or process constraint that must hold across "
            "flowsheets. Examples: 'max T = 200 °C', 'cooling water available "
            "at 30 °C', 'column diameter ≤ 1.5 m'."
        ),
        "parameters": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string",
                         "description": "The constraint in one concise sentence."},
            },
        },
    },
    {
        "name": "recall_memory",
        "description": (
            "Retrieve prior-session memory: design goals, constraints, and past "
            "flowsheet builds. Use when the user asks 'what did we decide?', "
            "'what flowsheets have I built?', or when you need to check whether "
            "a constraint applies before suggesting a design change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Optional substring filter. Omit to get the most recent entries."},
                "limit": {"type": "integer",
                          "description": "Max entries to return (default 5)."},
            },
        },
    },

    # ── Pinch Analysis ───────────────────────────────────────────────────────
    {
        "name": "pinch_analysis",
        "description": (
            "Perform Pinch Analysis (Linnhoff method) on the loaded flowsheet. "
            "Calculates minimum hot/cold utility requirements, pinch temperature, "
            "and potential energy savings through heat integration. "
            "Use when the user asks about energy optimisation, heat recovery, "
            "or 'how much can I save on heating/cooling costs?'. "
            "Returns QH_min, QC_min, pinch temperature, and potential savings %."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "min_approach_temp_C": {
                    "type": "number",
                    "description": "Minimum temperature approach ΔTmin in °C. Default 10°C. Use 3-5°C for cryogenic, 20-40°C for furnaces.",
                },
            },
            "required": [],
        },
    },

    # ── Multi-variable Optimisation ──────────────────────────────────────────
    {
        "name": "optimize_multivar",
        "description": (
            "Multi-variable optimisation using differential evolution. "
            "Simultaneously optimise 2-4 process variables (e.g. reflux ratio + feed stage) "
            "to minimise or maximise an output (e.g. reboiler duty, product purity). "
            "Use when optimize_parameter is not sufficient for the task. "
            "Note: each evaluation runs a full simulation — expect 50-200 evaluations total."
        ),
        "parameters": {
            "type": "object",
            "required": ["variables", "observe_tag", "observe_property"],
            "properties": {
                "variables": {
                    "type": "array",
                    "description": "List of variables to optimise.",
                    "items": {
                        "type": "object",
                        "required": ["tag", "property", "unit", "lower_bound", "upper_bound"],
                        "properties": {
                            "tag":         {"type": "string"},
                            "property":    {"type": "string"},
                            "unit":        {"type": "string"},
                            "lower_bound": {"type": "number"},
                            "upper_bound": {"type": "number"},
                        },
                    },
                },
                "observe_tag":      {"type": "string", "description": "Tag of the output stream or unit op to observe."},
                "observe_property": {"type": "string", "description": "Property to minimise/maximise."},
                "minimize":         {"type": "boolean", "description": "True to minimise (default), False to maximise."},
                "max_iterations":   {"type": "integer", "description": "Max DE generations. Default 100."},
                "population_size":  {"type": "integer", "description": "DE population multiplier per variable. Default 8."},
            },
        },
    },

    # ── Recycle Convergence Assistant ────────────────────────────────────────
    {
        "name": "initialize_recycle",
        "description": (
            "Seed a recycle stream with initial guess values to help the DWSIM solver converge. "
            "Recycle loops are the most common cause of convergence failure. "
            "Call this BEFORE save_and_solve when a flowsheet has a recycle loop. "
            "Provide temperature, pressure, and composition guesses close to expected steady-state values."
        ),
        "parameters": {
            "type": "object",
            "required": ["recycle_tag", "T_guess_C", "P_guess_bar", "composition"],
            "properties": {
                "recycle_tag":  {"type": "string", "description": "Tag of the recycle stream (MaterialStream)."},
                "T_guess_C":    {"type": "number", "description": "Initial temperature guess in °C."},
                "P_guess_bar":  {"type": "number", "description": "Initial pressure guess in bar."},
                "composition":  {
                    "type": "object",
                    "description": "Mole fraction dict, e.g. {\"Methanol\": 0.8, \"Water\": 0.2}",
                    "additionalProperties": {"type": "number"},
                },
                "solver": {
                    "type": "string",
                    "description": "Convergence algorithm: 'Wegstein' (default) or 'Broyden'.",
                    "enum": ["Wegstein", "Broyden"],
                },
            },
        },
    },

    # ── Bayesian Optimisation ───────────────────────────────────────────────
    {
        "name": "bayesian_optimize",
        "description": (
            "Bayesian Optimisation of a DWSIM simulation using a Gaussian Process surrogate "
            "and Expected Improvement acquisition. "
            "PREFER this over optimize_multivar when: (a) each simulation takes >5 seconds, "
            "(b) there are 1–4 continuous variables, (c) you need a convergence plot, "
            "or (d) the objective is noisy or discontinuous. "
            "Uses LHS warm-up (n_initial evaluations) then iterates BO for max_iter steps. "
            "Total DWSIM calls = n_initial + max_iter (default 5 + 20 = 25 max). "
            "Returns best variable values, best objective, convergence history, and optional PNG plot. "
            "Falls back gracefully when simulations fail — failed points are penalised, not discarded. "
            "Example: optimise reflux ratio + feed stage to maximise distillate purity in 25 evaluations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "array",
                    "description": (
                        "Variables to optimise. Each item: "
                        "{tag: str, property: str, unit: str, lower: float, upper: float}."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "tag":      {"type": "string", "description": "Stream or unit op tag"},
                            "property": {"type": "string", "description": "Property name (e.g. MassFlow, RefluxRatio)"},
                            "unit":     {"type": "string", "description": "Unit string (e.g. kg/h, -)"},
                            "lower":    {"type": "number", "description": "Lower bound"},
                            "upper":    {"type": "number", "description": "Upper bound"},
                        },
                        "required": ["tag", "property", "lower", "upper"],
                    },
                },
                "observe_tag": {
                    "type": "string",
                    "description": "Tag of stream or unit op whose property is the objective.",
                },
                "observe_property": {
                    "type": "string",
                    "description": "Property to observe as the objective (e.g. temperature_C, mole_fraction_Ethanol).",
                },
                "minimize": {
                    "type": "boolean",
                    "description": "True to minimise, False to maximise (default True).",
                },
                "n_initial": {
                    "type": "integer",
                    "description": "LHS warm-up evaluations (default 5). Increase to 8-10 for highly multimodal problems.",
                },
                "max_iter": {
                    "type": "integer",
                    "description": "BO iterations after warm-up (default 20). Total evals = n_initial + max_iter.",
                },
                "xi": {
                    "type": "number",
                    "description": "EI exploration bonus (default 0.01). Increase to 0.1 for more exploration.",
                },
                "seed": {
                    "type": "integer",
                    "description": "RNG seed for reproducibility (default 42).",
                },
                "save_plot": {
                    "type": "string",
                    "description": "Filepath to save convergence PNG (e.g. 'C:/tmp/bo_convergence.png'). Leave empty to skip.",
                },
            },
            "required": ["variables", "observe_tag", "observe_property"],
        },
    },

    # ── Monte Carlo Uncertainty Propagation ─────────────────────────────────
    {
        "name": "monte_carlo_study",
        "description": (
            "Monte Carlo uncertainty propagation — propagates feed uncertainty to output. "
            "Runs N simulations with randomly drawn input values (normal/uniform/triangular distributions). "
            "Returns: mean, std, p5/p25/p50/p75/p95 percentiles, 95% CI, histogram. "
            "Use when the user asks: 'What is the uncertainty in my output?', "
            "'How sensitive is the product purity to feed composition uncertainty?', "
            "or 'I need confidence intervals for my report'. "
            "For journal papers: use n_samples≥100 for reliable statistics. "
            "Note: each sample runs a full simulation — expect 5-30 minutes for n=100."
        ),
        "parameters": {
            "type": "object",
            "required": ["vary_params", "observe_tag", "observe_property"],
            "properties": {
                "vary_params": {
                    "type": "array",
                    "description": "Input variables with uncertainty distributions.",
                    "items": {
                        "type": "object",
                        "required": ["tag", "property", "unit", "distribution"],
                        "properties": {
                            "tag":          {"type": "string"},
                            "property":     {"type": "string"},
                            "unit":         {"type": "string"},
                            "distribution": {"type": "string", "enum": ["normal", "uniform", "triangular"]},
                            "mean":         {"type": "number", "description": "For normal distribution"},
                            "std":          {"type": "number", "description": "Standard deviation (normal)"},
                            "low":          {"type": "number", "description": "Lower bound (uniform/triangular)"},
                            "high":         {"type": "number", "description": "Upper bound (uniform/triangular)"},
                            "mode":         {"type": "number", "description": "Most likely value (triangular)"},
                        },
                    },
                },
                "observe_tag":      {"type": "string",  "description": "Output stream or unit op tag"},
                "observe_property": {"type": "string",  "description": "Property to observe"},
                "n_samples":        {"type": "integer", "description": "Number of Monte Carlo samples. Default 100, use 30 for quick test, 200+ for journal."},
            },
        },
    },

    # ── Compound Property Database ───────────────────────────────────────────
    {
        "name": "get_compound_properties",
        "description": (
            "Return critical thermodynamic constants for a compound from the DWSIM database: "
            "Tc, Pc, acentric factor (ω), normal boiling point, MW, ΔHf°, ΔGf°, CAS number. "
            "Use this to: (1) verify a compound exists before new_flowsheet, "
            "(2) justify property package selection (e.g. Tr = T/Tc check), "
            "(3) include compound data in reports."
        ),
        "parameters": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Compound name as in DWSIM database, e.g. 'Water', 'Methanol', 'Ethylene'.",
                },
            },
        },
    },

    # ── Thermodynamic Property Database ──────────────────────────────────────
    {
        "name": "lookup_compound_properties",
        "description": (
            "Look up exact thermodynamic properties for a pure compound from the "
            "built-in DIPPR/DECHEMA property database (38 compounds). "
            "Returns critical properties (Tc, Pc, Vc, omega), Antoine vapor pressure "
            "constants, liquid density, heat of vaporization, heat capacity, and "
            "Henry's law constant in water. "
            "Use this BEFORE setting up any simulation to get correct Tc, Pc, omega "
            "for the property package, and to verify boiling points and phase behavior. "
            "Accepts common names, abbreviations (EtOH, IPA, THF, MeCN), and CAS numbers."
        ),
        "parameters": {
            "type": "object",
            "required": ["compound"],
            "properties": {
                "compound": {
                    "type": "string",
                    "description": (
                        "Compound name, alias, abbreviation, or CAS number. "
                        "Examples: 'ethanol', 'EtOH', 'IPA', 'CO2', 'H2S', 'THF', "
                        "'2-propanol', '64-17-5' (CAS for ethanol)."
                    ),
                },
                "properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which property groups to return. Options: "
                        "'critical' (Tc, Pc, Vc, omega, Tb), "
                        "'antoine' (vapor pressure constants, Psat at 25°C), "
                        "'density' (liquid density at 25°C), "
                        "'thermal' (dHvap, Cp liquid), "
                        "'henry' (Henry's law constant in water). "
                        "Omit or pass ['all'] to get everything."
                    ),
                },
            },
        },
    },
    {
        "name": "batch_lookup_properties",
        "description": (
            "Batch version of lookup_compound_properties — look up properties for "
            "MULTIPLE compounds in a SINGLE call. Use this when building a flowsheet "
            "with 2+ compounds: it saves 2-5 LLM iterations vs calling "
            "lookup_compound_properties once per compound. "
            "Returns a dict keyed by compound name with the same property groups."
        ),
        "parameters": {
            "type": "object",
            "required": ["compounds"],
            "properties": {
                "compounds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of compound names, aliases, or CAS numbers. "
                        "Example: ['methanol', 'ethanol', 'water', 'acetone']."
                    ),
                },
                "properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which property groups to return for each compound. "
                        "Same options as lookup_compound_properties: 'critical', "
                        "'antoine', 'density', 'thermal', 'henry'. "
                        "Omit for everything."
                    ),
                },
            },
        },
    },
    {
        "name": "lookup_binary_parameters",
        "description": (
            "Look up binary interaction parameters (BIPs) for a pair of compounds. "
            "Supports NRTL (tau12, tau21, alpha), UNIQUAC (u12-u22, u21-u11 in K), "
            "and temperature-dependent NRTL (a12, b12, a21, b21 where tau_ij = a_ij + b_ij/T). "
            "Use this when setting up NRTL or UNIQUAC property packages to get "
            "literature-fitted BIPs instead of estimating from UNIFAC. "
            "Covers common binary pairs including alcohol-water, ketone-water, "
            "ester-water, and aromatic-solvent systems. "
            "If the pair is not in the database, returns a message explaining "
            "how to estimate with UNIFAC or fit to experimental data."
        ),
        "parameters": {
            "type": "object",
            "required": ["comp1", "comp2"],
            "properties": {
                "comp1": {
                    "type": "string",
                    "description": "First compound name or alias (e.g., 'ethanol', 'acetone', 'THF').",
                },
                "comp2": {
                    "type": "string",
                    "description": "Second compound name or alias (e.g., 'water', 'methanol').",
                },
                "model": {
                    "type": "string",
                    "enum": ["nrtl", "uniquac", "nrtl_tdep"],
                    "description": (
                        "Thermodynamic model for which BIPs are needed. "
                        "'nrtl' — fixed-T NRTL (tau12, tau21, alpha); "
                        "'uniquac' — UNIQUAC energy parameters (u12-u22, u21-u11 in K); "
                        "'nrtl_tdep' — temperature-dependent NRTL (tau_ij = a_ij + b_ij/T)."
                    ),
                },
            },
        },
    },
    {
        "name": "compute_vapor_pressure",
        "description": (
            "Compute the vapor pressure (saturation pressure) of a pure compound "
            "at a given temperature using the Antoine equation. "
            "Returns Psat in mmHg, bar, and kPa. "
            "Useful for checking bubble/dew points, relative volatility, and "
            "distillation feasibility before running a full simulation. "
            "Example: compute_vapor_pressure('ethanol', 78.37) returns ~760 mmHg "
            "(confirms normal boiling point at 1 atm)."
        ),
        "parameters": {
            "type": "object",
            "required": ["compound", "T_C"],
            "properties": {
                "compound": {
                    "type": "string",
                    "description": "Compound name or alias (e.g., 'ethanol', 'benzene', 'water').",
                },
                "T_C": {
                    "type": "number",
                    "description": "Temperature in degrees Celsius at which to compute Psat.",
                },
            },
        },
    },
    {
        "name": "search_compound_database",
        "description": (
            "Search the thermodynamic property database by partial name, alias, or CAS number. "
            "Use this when you are not sure of the exact compound name — it returns up to "
            "10 matching compounds with their CAS numbers and molecular weights. "
            "Supports partial matching: 'propan' matches propane, 1-propanol, 2-propanol."
        ),
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Partial name, alias, abbreviation, or CAS number to search for.",
                },
            },
        },
    },
    # ── Industrial Bridge Upgrades ─────────────────────────────────────────────
    {
        "name": "robust_solve",
        "description": (
            "Enhanced save_and_solve with adaptive convergence strategies for industrial flowsheets. "
            "Automatically retries with different strategies when standard solve fails. "
            "Use instead of save_and_solve when dealing with: recycle loops, distillation columns, "
            "reactive systems, or any flowsheet that failed to converge on the first attempt. "
            "strategy='robust' tries 3 reload+solve cycles. strategy='aggressive' also perturbs feeds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_attempts": {"type": "integer", "description": "Max solve attempts (default 3)", "default": 3},
                "strategy": {"type": "string", "enum": ["standard", "robust", "aggressive"],
                             "description": "standard=1 attempt, robust=3 reloads, aggressive=5 with feed perturbation"},
            },
        },
    },
    {
        "name": "initialize_distillation",
        "description": (
            "Initialize and converge a rigorous distillation column with automatic algorithm escalation. "
            "Tries Inside-Out → Burningham-Otto → Sum-Rates algorithms on failure. "
            "Use this for ALL rigorous DistillationColumn and AbsorptionColumn unit ops. "
            "Always provide T_top_C (estimated condenser temperature) and T_bot_C (estimated reboiler temperature) "
            "to seed the temperature profile. Much more reliable than save_and_solve for columns."
        ),
        "parameters": {
            "type": "object",
            "required": ["column_tag"],
            "properties": {
                "column_tag": {"type": "string", "description": "Tag of the DistillationColumn or AbsorptionColumn"},
                "T_top_C": {"type": "number", "description": "Estimated top (condenser) temperature in °C"},
                "T_bot_C": {"type": "number", "description": "Estimated bottom (reboiler) temperature in °C"},
                "algorithm": {"type": "string", "enum": ["auto", "IO", "BO", "SR"],
                              "description": "auto=escalate IO→BO→SR on failure, IO=Inside-Out (default), BO=Burningham-Otto (wide-boiling), SR=Sum-Rates (non-ideal)"},
                "reflux_ratio": {"type": "number", "description": "Reflux ratio to set before solving (optional, reduces 10% each retry)"},
                "max_attempts": {"type": "integer", "description": "Maximum convergence attempts (default 4)"},
            },
        },
    },
    {
        "name": "optimize_constrained",
        "description": (
            "Multi-variable optimization WITH inequality constraints — the industrial-grade optimizer. "
            "Use when you need to meet product specifications while optimizing yield or energy. "
            "Example: maximize H2 yield subject to CO < 10 ppm and T < 950°C. "
            "Uses differential evolution with penalty functions for constraint handling. "
            "constraints format: [{tag, property, unit, operator, value}] where operator is '>=' or '<='."
        ),
        "parameters": {
            "type": "object",
            "required": ["variables", "observe_tag", "observe_property"],
            "properties": {
                "variables": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {tag, property, unit, lower, upper} dicts for decision variables",
                },
                "observe_tag": {"type": "string", "description": "Stream or unit op tag to observe as objective"},
                "observe_property": {"type": "string", "description": "Property to optimize (e.g. mole_fraction_h2)"},
                "constraints": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {tag, property, unit, operator, value} — e.g. {'tag':'Product','property':'CO_ppm','operator':'<=','value':10}",
                },
                "minimize": {"type": "boolean", "description": "True=minimize, False=maximize (default True)"},
                "max_iter": {"type": "integer", "description": "Max optimizer iterations (default 100)"},
                "population_size": {"type": "integer", "description": "Differential evolution population size (default 15)"},
                "seed": {"type": "integer", "description": "Random seed for reproducibility (default 42)"},
            },
        },
    },
    {
        "name": "optimize_multiobjective",
        "description": (
            "Multi-objective optimization generating a Pareto trade-off curve. "
            "Use when there are competing objectives: e.g. maximize H2 yield AND minimize steam consumption. "
            "Returns n_points Pareto-optimal solutions showing the trade-off between objectives. "
            "Uses weighted sum scalarization with differential evolution at each weight combination."
        ),
        "parameters": {
            "type": "object",
            "required": ["variables", "objectives"],
            "properties": {
                "variables": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {tag, property, unit, lower, upper} decision variables",
                },
                "objectives": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {tag, property, unit, minimize, weight_start, weight_end} objectives. weight_start/end define how the weight varies across the Pareto front.",
                },
                "n_points": {"type": "integer", "description": "Number of Pareto front points (default 10)"},
                "max_iter_per_point": {"type": "integer", "description": "Max optimizer iterations per Pareto point (default 50)"},
                "seed": {"type": "integer", "description": "Reproducibility seed (default 42)"},
            },
        },
    },
    {
        "name": "parametric_study_2d",
        "description": (
            "Two-variable parametric study generating a full response surface (RSM-equivalent). "
            "Use when you need to understand how TWO inputs interact to affect one output. "
            "Equivalent to what RSM / Central Composite Design captures in papers. "
            "Returns an n1×n2 matrix of results — use to identify optimal operating region. "
            "Example: vary temperature [700,800,900,1000°C] × pressure [10,12,14,16 bar] "
            "→ observe H2 yield (16 simulations total)."
        ),
        "parameters": {
            "type": "object",
            "required": ["vary1_tag","vary1_property","vary1_unit","vary1_values",
                         "vary2_tag","vary2_property","vary2_unit","vary2_values",
                         "observe_tag","observe_property"],
            "properties": {
                "vary1_tag":        {"type": "string"},
                "vary1_property":   {"type": "string"},
                "vary1_unit":       {"type": "string"},
                "vary1_values":     {"type": "array", "items": {"type": "number"},
                                     "description": "List of values for variable 1"},
                "vary2_tag":        {"type": "string"},
                "vary2_property":   {"type": "string"},
                "vary2_unit":       {"type": "string"},
                "vary2_values":     {"type": "array", "items": {"type": "number"},
                                     "description": "List of values for variable 2"},
                "observe_tag":      {"type": "string"},
                "observe_property": {"type": "string"},
            },
        },
    },
]
