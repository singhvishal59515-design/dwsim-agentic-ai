/** Shared TypeScript types for DWSIM Agentic AI React UI */

export interface StreamProps {
  temperature_C?:    number;
  temperature_K?:    number;
  pressure_bar?:     number;
  pressure_Pa?:      number;
  mass_flow_kgh?:    number;
  mass_flow_kgs?:    number;
  molar_flow_kmolh?: number;
  vapor_fraction?:   number;
  mole_fractions?:   Record<string, number>;
  _sf05_corrected?:  boolean;
  [key: string]: any;
}

export interface FlowsheetState {
  name:             string;
  property_package: string;
  streams:          string[];
  unit_ops:         string[];
  object_types:     Record<string, string>;
  compounds:        string[];
  converged:        boolean | null;
  path:             string;
}

export interface SafetyWarning {
  code:        string;
  severity:    'SILENT' | 'WARNING' | 'LOUD';
  description: string;
  evidence?:   string;
  stream?:     string;
  auto_fixed?: boolean;
  fix?:        string;
}

export interface PreSolveFailure {
  code:        string;
  severity:    string;
  description: string;
  fix:         string;
}

export interface SimulationResult {
  success:             boolean;
  safety_status?:      'PASSED' | 'VIOLATIONS_DETECTED' | 'PRE_SOLVE_VIOLATION';
  safety_warnings?:    SafetyWarning[];
  pre_solve_failures?: PreSolveFailure[];
  stream_results?:     Record<string, StreamProps>;
  sf05_auto_corrections?: number;
  sf05_note?:          string;
  error?:              string;
  converged?:          boolean;
}

export interface ToolCallEvent {
  name:    string;
  args?:   Record<string, any>;
  result?: SimulationResult & Record<string, any>;
}

export interface ChatMessage {
  role:       'user' | 'assistant' | 'tool' | 'error';
  content:    string;
  tools?:     ToolCallEvent[];
  ts?:        number;
  sessionId?: string;                          // backend session_id for feedback
  feedback?:  'thumbs_up' | 'thumbs_down';    // human feedback recorded
}

export interface LLMStatus {
  provider: string;
  model:    string;
}

export interface ParametricRow {
  input:   number;
  outputs: Record<string, number>;
}

export interface ParametricData {
  input_label:   string;
  output_labels: string[];
  table:         ParametricRow[];
}

export interface FlowsheetFile {
  name:      string;
  path:      string;
  size:      number;
  modified?: string;
  streams?:  number;
  unit_ops?: number;
}

export interface EconomicsResult {
  capex?:      Record<string, any>;
  opex?:       Record<string, any>;
  revenue?:    Record<string, any>;
  npv_rows?:   Array<{ year: number; cumulative_npv: number }>;
  payback_yr?: number;
  tcc?:        number;
}

export interface ConvergenceData {
  success:        boolean;
  all_converged:  boolean;
  not_converged:  string[];
  errors?:        string[];
  auto_corrected?: boolean;
  fixes_applied?:  string[];
}

export interface UnitOpData {
  tag:      string;
  type:     string;
  category: string;
  summary:  Record<string, any>;
}

export interface ReportCard {
  title:       string;
  pdf_path:    string;
  timestamp:   string;
  data_points?: number;
  plot_count?:  number;
  stats?:       any;
  sections?:    string[];
}

export interface DiagramNode {
  id:       string;
  type:     string;
  category: string;
  x?:       number;
  y?:       number;
}

export interface DiagramConnection {
  from: string;
  to:   string;
  type?: string;
}

export interface DiagramData {
  name:        string;
  nodes:       DiagramNode[];
  connections: DiagramConnection[];
}

export interface PropertyChange {
  tag:       string;
  property:  string;
  oldValue:  any;
  newValue:  any;
  unit?:     string;
}

export const PROVIDER_COLOR: Record<string, string> = {
  openai:    '#10a37f',
  groq:      '#f55036',
  gemini:    '#4285f4',
  anthropic: '#cc785c',
  ollama:    '#9b59b6',
};

export const PROVIDER_LABEL: Record<string, string> = {
  openai:    'OpenAI',
  groq:      'Groq',
  gemini:    'Gemini',
  anthropic: 'Anthropic',
  ollama:    'Ollama (Local)',
};

export const QUICK_TEMPLATES = [
  { key: 'water-heater',   label: 'Water Heater',
    prompt: 'Create a water heating process from 25°C to 80°C at 1 atm, 1 kg/s pure water using Steam Tables (IAPWS-IF97).' },
  { key: 'methanol-hx',    label: 'Methanol HX',
    prompt: 'Create a heat exchanger: Hot side Methanol at 80°C 5bar 25000 kg/h, Cold side Water at 10°C 1atm 15000 kg/h. Use NRTL.' },
  { key: 'ethanol-distill',label: 'Ethanol Distillation',
    prompt: 'Create a distillation column to separate ethanol-water mixture. Feed: 50 mol% ethanol, 50 mol% water at 78°C, 1 atm, 100 kmol/h. Use NRTL.' },
  { key: 'gas-compress',   label: 'Gas Compressor',
    prompt: 'Create a compressor system: Air at 25°C 1 atm 10 kg/s, compress to 5 atm. Use Peng-Robinson.' },
  { key: 'flash-sep',      label: 'Flash Separator',
    prompt: 'Create a flash separator: Feed hydrocarbon mixture (methane 0.3, ethane 0.3, propane 0.4 mole frac) at 25°C, 10 bar, 1000 kg/h. Use Peng-Robinson.' },
  { key: 'conv-reactor',   label: 'Conversion Reactor',
    prompt: 'Create a conversion reactor for A→B at 200°C, 5 bar, 1000 kg/h. Feed: 100% A. Conversion 85%. Use Peng-Robinson.' },
  { key: 'pump-valve',     label: 'Pump + Valve',
    prompt: 'Create a pump-valve system: Water at 25°C 1 atm 5 kg/s. Pump to 5 bar, then throttle through valve to 2 bar. Use Steam Tables.' },
];
