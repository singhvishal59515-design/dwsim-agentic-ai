import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../utils/api';
import { FlowsheetState, StreamProps, ConvergenceData, UnitOpData } from '../types';

export interface UseSimulationReturn {
  state:          FlowsheetState | null;
  results:        Record<string, StreamProps>;
  diagram:        any | null;
  convergence:    ConvergenceData | null;
  unitOps:        Record<string, UnitOpData>;
  feedWarnings:   string[];
  loadedSheets:   string[];
  loading:        boolean;
  error:          string | null;
  refresh:        () => Promise<void>;
  loadFlowsheet:  (path: string) => Promise<void>;
  runSim:         () => Promise<void>;
  saveSim:        (push?: boolean) => Promise<void>;
  switchSheet:    (alias: string) => Promise<void>;
  setError:       (e: string | null) => void;
}

export function useSimulation(): UseSimulationReturn {
  const [state,        setState]        = useState<FlowsheetState | null>(null);
  const [results,      setResults]      = useState<Record<string, StreamProps>>({});
  const [diagram,      setDiagram]      = useState<any | null>(null);
  const [convergence,  setConvergence]  = useState<ConvergenceData | null>(null);
  const [unitOps,      setUnitOps]      = useState<Record<string, UnitOpData>>({});
  const [feedWarnings, setFeedWarnings] = useState<string[]>([]);
  const [loadedSheets, setLoadedSheets] = useState<string[]>([]);
  const [loading,      setLoading]      = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const wsRef = useRef<{ close: () => void } | null>(null);

  const refresh = useCallback(async () => {
    try {
      // Fetch health (name, property_package), objects (stream/unit_op arrays),
      // and results in parallel — these are the most important for UI state.
      const [health, objs, res, conv] = await Promise.all([
        api.health().catch(() => null),
        api.listObjects().catch(() => null),
        api.getResults().catch(() => null),
        api.checkConvergence().catch(() => null),
      ]);

      // Build FlowsheetState from /health + /flowsheet/objects
      const name = (health?.flowsheet || '') as string;
      const pkg  = (health?.property_package || '') as string;

      if (objs?.success && Array.isArray(objs.objects) && objs.objects.length > 0) {
        const streams: string[] = [];
        const unit_ops: string[] = [];
        const object_types: Record<string, string> = {};

        for (const o of objs.objects) {
          if (!o.tag) continue;
          object_types[o.tag] = o.type || '';
          if (o.category === 'stream') streams.push(o.tag);
          else unit_ops.push(o.tag);
        }

        setState({
          name,
          property_package: pkg,
          streams,
          unit_ops,
          object_types,
          compounds: [],
          converged: null,
          path: '',
        });
      } else if (name) {
        // No objects yet (empty flowsheet) — at least show the name
        setState(prev => prev
          ? { ...prev, name, property_package: pkg }
          : { name, property_package: pkg, streams: [], unit_ops: [], object_types: {}, compounds: [], converged: null, path: '' }
        );
      }

      if (res?.success && res.stream_results) setResults(res.stream_results);

      // Convergence
      if (conv?.success) {
        // not_converged is [{tag, missing:[...]}]; flatten to display strings
        const nc: string[] = (conv.not_converged || []).map((x: any) =>
          typeof x === 'string' ? x : `${x.tag} (missing: ${(x.missing || []).join(', ')})`
        );
        setConvergence({
          success:       true,
          all_converged: conv.all_converged ?? true,
          not_converged: nc,
          errors:        (conv.inaccessible || []).map((t: string) => `${t}: inaccessible`),
          auto_corrected: conv.auto_corrected,
          fixes_applied:  conv.fixes_applied,
        });
      }

      // Secondary fetches — fire and forget so they don't block the main refresh
      api.getMeta().then((m: any) => {
        if (m?.path) setState(prev => prev ? { ...prev, path: m.path } : null);
      }).catch(() => {});

      api.getDiagram().then((d: any) => {
        if (d) setDiagram(d);
      }).catch(() => {});

      api.getUnitOps().then((d: any) => {
        if (d?.success && Array.isArray(d.unit_ops)) {
          const map: Record<string, UnitOpData> = {};
          for (const op of d.unit_ops) {
            map[op.tag] = {
              tag:      op.tag,
              type:     op.type     || 'Unknown',
              category: 'unit_op',
              summary:  op.properties || op.summary || {},
            };
          }
          setUnitOps(map);
        }
      }).catch(() => {});

      api.listLoaded().then((d: any) => {
        if (d?.loaded) setLoadedSheets(Object.keys(d.loaded));
      }).catch(() => {});

      api.validateFeeds().then((d: any) => {
        if (d?.warnings?.length) setFeedWarnings(d.warnings);
        else setFeedWarnings([]);
      }).catch(() => {});

    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const loadFlowsheet = useCallback(async (path: string) => {
    setLoading(true); setError(null);
    try { await api.loadFlowsheet(path); await refresh(); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, [refresh]);

  const runSim = useCallback(async () => {
    setLoading(true); setError(null);
    try { await api.runSimulation(); await refresh(); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, [refresh]);

  const saveSim = useCallback(async (push = false) => {
    setLoading(true); setError(null);
    try { await api.saveFlowsheet(undefined, push); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  const switchSheet = useCallback(async (alias: string) => {
    setLoading(true);
    try { await api.switchFlowsheet(alias); await refresh(); }
    catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, [refresh]);

  useEffect(() => {
    refresh();
    wsRef.current = api.flowsheetWs((evt: any) => {
      if (['modified', 'loaded', 'solved', 'file_event'].includes(evt.type)) refresh();
    });
    return () => wsRef.current?.close();
  }, [refresh]);

  return {
    state, results, diagram, convergence, unitOps, feedWarnings, loadedSheets,
    loading, error, refresh, loadFlowsheet, runSim, saveSim, switchSheet, setError,
  };
}
