/**
 * api.ts — Complete REST + SSE + WebSocket client for the DWSIM FastAPI backend
 */

const BASE = process.env.REACT_APP_API_URL || '';

let _apiKey = localStorage.getItem('dwsim_api_key') || '';

export function setApiKey(key: string) {
  _apiKey = key;
  localStorage.setItem('dwsim_api_key', key);
}

export function getApiKey() { return _apiKey; }

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json', ...extra };
  if (_apiKey) h['X-API-Key'] = _apiKey;
  return h;
}

async function _get(path: string) {
  const res = await fetch(`${BASE}${path}`, { headers: headers() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function _post(path: string, body?: object) {
  const res = await fetch(`${BASE}${path}`, {
    method:  'POST',
    headers: headers(),
    body:    body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function _delete(path: string) {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE', headers: headers() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // ── health ────────────────────────────────────────────────────────────────
  health:      () => _get('/health'),
  diagnostics: (skipProviders = true) =>
    _get(`/diagnostics?skip_providers=${skipProviders}`),

  // ── chat ──────────────────────────────────────────────────────────────────
  chatReset: () => _post('/chat/reset'),

  chatStream(
    message: string,
    onEvent: (evt: { type: string; data: any }) => void,
    opts?: { autoReflect?: boolean; reflectClose?: boolean }
  ): () => void {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${BASE}/chat/stream`, {
          method:  'POST',
          headers: headers(),
          signal:  controller.signal,
          body:    JSON.stringify({
            message,
            auto_reflect:        opts?.autoReflect  ?? false,
            reflect_close_first: opts?.reflectClose ?? false,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          onEvent({ type: 'error', data: err.detail || `HTTP ${res.status}` });
          return;
        }
        if (!res.body) { onEvent({ type: 'error', data: 'No response body' }); return; }
        const reader  = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() || '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore */ }
          }
        }
      } catch (e: any) {
        if (e.name !== 'AbortError') onEvent({ type: 'error', data: e.message });
      }
    })();
    return () => controller.abort();
  },

  // ── flowsheet ─────────────────────────────────────────────────────────────
  findFlowsheets: ()                    => _get('/find'),
  loadFlowsheet:  (path: string, alias?: string) =>
                    _post('/flowsheet/load', { path, alias }),
  saveFlowsheet:  (path?: string, push_to_gui = false) =>
                    _post('/flowsheet/save', { path, push_to_gui }),
  runSimulation:  ()                    => _post('/flowsheet/run'),
  listObjects:    ()                    => _get('/flowsheet/objects'),
  getResults:     ()                    => _get('/flowsheet/results'),
  getDiagram:     ()                    => _get('/flowsheet/diagram'),
  getTopology:    ()                    => _get('/flowsheet/topology'),
  getMeta:        ()                    => _get('/flowsheet/meta'),
  getPropertyPackage: ()                => _get('/flowsheet/package'),
  validateFeeds:  ()                    => _post('/flowsheet/validate'),
  checkConvergence: ()                  => _get('/flowsheet/convergence'),
  listLoaded:     ()                    => _get('/flowsheet/loaded'),
  switchFlowsheet: (alias: string)      =>
                    _post(`/flowsheet/switch?alias=${encodeURIComponent(alias)}`),
  listTemplates:  ()                    => _get('/flowsheet/templates'),
  createFromTemplate: (name: string, fs_name?: string) =>
                    _post('/flowsheet/create-from-template', { name, flowsheet_name: fs_name }),
  listBackups:    ()                    => _get('/flowsheet/backups'),
  restoreBackup:  (path: string)        => _post('/flowsheet/backups/restore', { path }),
  pushToGui:      (path?: string, close_first = false) =>
                    _post('/flowsheet/push-to-gui', { path, close_first }),
  getUnitOps:     ()                    => _get('/flowsheet/unitops'),
  guiState:       ()                    => _get('/flowsheet/gui-state'),

  // ── stream / unit-op ──────────────────────────────────────────────────────
  getStreamProps:       (tag: string)   => _post('/stream/properties', { tag }),
  setStreamProperty:    (tag: string, property_name: string, value: number, unit = '') =>
                          _post('/stream/set_property', { tag, property_name, value, unit }),
  setStreamComposition: (tag: string, compositions: Record<string, number>) =>
                          _post('/stream/set_composition', { tag, compositions }),
  setUnitOpProperty:    (tag: string, property_name: string, value: string) =>
                          _post('/unitop/set_property', { tag, property_name, value }),
  getObjectProps:       (tag: string)   => _post('/object/properties', { tag }),

  // ── parametric & optimise ─────────────────────────────────────────────────
  parametricStudy: (req: {
    vary_tag: string; vary_property: string; vary_unit: string;
    values: number[]; observe_tag: string; observe_property: string;
  }) => _post('/parametric', req),

  optimize: (req: {
    vary_tag: string; vary_property: string; vary_unit: string;
    lower_bound: number; upper_bound: number;
    observe_tag: string; observe_property: string;
    minimize?: boolean;
  }) => _post('/optimize', req),

  // ── economic analysis ─────────────────────────────────────────────────────
  economicsDefaults: ()                        => _get('/economics/defaults'),
  economicsEstimate: (params: Record<string, any>) => _post('/economics/estimate', params),

  // ── report ────────────────────────────────────────────────────────────────
  generateReport: (req: Record<string, any>) => _post('/report/generate', req),
  downloadReport: (path: string) =>
    `${BASE}/report/download?path=${encodeURIComponent(path)}`,

  // ── LLM provider ──────────────────────────────────────────────────────────
  llmStatus:    ()                                 => _get('/llm/status'),
  llmSwitch:    (provider: string, model: string)  =>
                  _post(`/llm/switch?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  groqModels:   ()                                 => _get('/llm/groq/models'),
  ollamaModels: ()                                 => _get('/llm/ollama/models'),

  // ── sessions ──────────────────────────────────────────────────────────────
  listSessions: ()               => _get('/sessions'),
  saveSession:  (name: string)   =>
                  _post(`/sessions/save?name=${encodeURIComponent(name)}`),
  loadSession:  (path: string)   =>
                  _post(`/sessions/load?path=${encodeURIComponent(path)}`),

  // ── memory ────────────────────────────────────────────────────────────────
  memoryRecent: (limit = 10) => _get(`/memory/recent?limit=${limit}`),
  memoryGoals:  ()            => _get('/memory/goals'),

  // ── accuracy ─────────────────────────────────────────────────────────────
  accuracySummary:    () => _get('/accuracy/summary'),
  accuracyCompare:    (ref_id: string) =>
                        _post('/accuracy/compare', { ref_id, auto_query: true }),
  accuracyCompareRaw: (ref_id: string, auto_query = false) =>
                        _post('/accuracy/compare', { ref_id, auto_query, use_last_agent_answer: !auto_query }),
  accuracyRefSets:    () => _get('/accuracy/reference'),
  accuracyDeleteRef:  (ref_id: string) => _delete(`/accuracy/reference/${encodeURIComponent(ref_id)}`),

  // ── safety ────────────────────────────────────────────────────────────────
  safetyCatalogue: () => _get('/safety/catalogue'),
  safetyValidate:  (stream_results: any) =>
                     _post('/safety/validate', { stream_results }),

  // ── benchmark ─────────────────────────────────────────────────────────────
  benchmarkTasks: () => _get('/benchmark/tasks'),
  benchmarkRun:   (task_id: string) =>
                    _post('/benchmark/run', { task_id }),

  // ── export ────────────────────────────────────────────────────────────────
  exportExcel: () => `${BASE}/results/export/excel`,
  exportCsv:   () => `${BASE}/results/export/csv`,

  // ── flowsheet browser ─────────────────────────────────────────────────────
  scanFlowsheets:  (max?: number) =>
                     _get(`/flowsheets/scan${max ? `?max_files=${max}` : ''}`),
  scanCustomPath:  (directory: string) =>
                     _get(`/flowsheets/scan/path?directory=${encodeURIComponent(directory)}`),
  loadByPath:      (path: string, alias?: string) =>
                     _post('/flowsheets/load-by-path', { path, alias }),

  // ── compounds / packages ──────────────────────────────────────────────────
  getCompounds:        (search = '') => _get(`/compounds?search=${encodeURIComponent(search)}`),
  getPropertyPackages: ()            => _get('/property-packages'),

  // ── knowledge ─────────────────────────────────────────────────────────────
  knowledgeSearch: (q: string, k = 5) => _get(`/knowledge?q=${encodeURIComponent(q)}&k=${k}`),
  knowledgeTopics: () => _get('/knowledge/topics'),

  // ── eval ─────────────────────────────────────────────────────────────────
  evalMetrics:    () => _get('/eval/metrics'),
  evalReliability: () => _get('/eval/reliability'),
  evalFailures:   () => _get('/eval/failures'),
  evalClear:      () => _delete('/eval/clear'),

  // ── new analysis tools ────────────────────────────────────────────────────
  pinchAnalysis:     (minApproachTempC = 10) =>
                       _get(`/flowsheet/pinch?min_approach_temp_C=${minApproachTempC}`),
  compareFlowsheets: ()                      => _get('/flowsheet/compare'),
  compoundProperties:(name: string)          =>
                       _get(`/compounds/${encodeURIComponent(name)}/properties`),
  monteCarlo:        (req: Record<string, any>) => _post('/monte-carlo', req),
  ablationSummary:   ()                      => _get('/ablation/summary'),
  ablationConfigs:   ()                      => _get('/ablation/configs'),
  ablationRun:       (req: Record<string, any>) => _post('/ablation/run', req),
  reproducibility:   ()                      => _get('/reproducibility/last-turn'),
  replayTurns:       (sessionId = '', n = 30) =>
                       _get(`/reproducibility/turns?session_id=${encodeURIComponent(sessionId)}&n=${n}`),
  replayExport:      (sessionId: string)      =>
                       _get(`/reproducibility/session/${encodeURIComponent(sessionId)}/export`),

  // ── Diagnostics (additional routes) ──────────────────────────────────────
  diagnosticsVersion:  ()                    => _get('/diagnostics/version'),
  diagnosticsProviders:()                    => _get('/diagnostics/providers'),

  // ── Session memory (additional) ───────────────────────────────────────────
  memorySearch:      (q: string)            => _get(`/memory/search?q=${encodeURIComponent(q)}`),
  memoryRecord:      (type: string, payload: Record<string, any>) =>
                       _post('/memory/record', { entry_type: type, ...payload }),

  // ── Optimisation (additional) ─────────────────────────────────────────────
  optimizeMultivar:  (req: Record<string, any>) => _post('/optimize/multivar', req),
  bayesianOptimize:  (req: Record<string, any>) => _post('/optimize/bayesian', req),

  // ── Accuracy (additional) ─────────────────────────────────────────────────
  accuracyCapture:   (req: Record<string, any>) => _post('/accuracy/capture', req),
  accuracyComparisons:()                     => _get('/accuracy/comparisons'),
  benchmarkSummary:  ()                      => _get('/benchmark/summary'),

  // ── Flowsheet (additional) ────────────────────────────────────────────────
  flowsheetTopology: ()                      => _get('/flowsheet/topology'),
  flowsheetValidate: ()                      => _get('/flowsheet/validate'),
  initRecycle:       (req: Record<string, any>) => _post('/flowsheet/initialize-recycle', req),
  reportGenerate:    (req: Record<string, any>) => _post('/report/generate', req),

  // ── WebSocket ─────────────────────────────────────────────────────────────
  flowsheetWs(onEvent: (evt: any) => void): { close: () => void } {
    const wsBase = (BASE || `http://${window.location.host}`)
      .replace(/^https/, 'wss').replace(/^http/, 'ws');
    const ws = new WebSocket(`${wsBase}/ws/flowsheets`);
    ws.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    ws.onerror = () => onEvent({ type: 'ws_error' });
    ws.onclose = () => onEvent({ type: 'ws_close' });
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping');
    }, 30_000);
    return { close: () => { clearInterval(ping); ws.close(); } };
  },
};
