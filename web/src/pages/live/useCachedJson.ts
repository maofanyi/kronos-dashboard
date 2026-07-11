import { useCallback, useEffect, useState } from "react";

type QueryState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

const responseCache = new Map<string, unknown>();
const inFlightQueries = new Map<string, Promise<unknown>>();

function requestJson<T>(url: string): Promise<T> {
  const cached = responseCache.get(url);
  if (cached !== undefined) return Promise.resolve(cached as T);

  const pending = inFlightQueries.get(url);
  if (pending) return pending as Promise<T>;

  const request = fetch(url)
    .then(async (response) => {
      if (!response.ok) throw new Error(`请求失败 (${response.status})`);
      const payload = await response.json() as T;
      responseCache.set(url, payload);
      return payload;
    })
    .finally(() => {
      inFlightQueries.delete(url);
    });

  inFlightQueries.set(url, request);
  return request;
}

export function useCachedJson<T>(url: string | null) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [state, setState] = useState<QueryState<T>>({
    data: url && responseCache.has(url) ? responseCache.get(url) as T : null,
    loading: Boolean(url && !responseCache.has(url)),
    error: null,
  });

  useEffect(() => {
    let active = true;
    if (!url) {
      setState({ data: null, loading: false, error: null });
      return () => {
        active = false;
      };
    }

    const cached = responseCache.get(url);
    if (cached !== undefined) {
      setState({ data: cached as T, loading: false, error: null });
      return () => {
        active = false;
      };
    }

    setState({ data: null, loading: true, error: null });
    void requestJson<T>(url)
      .then((data) => {
        if (active) setState({ data, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!active) return;
        setState({
          data: null,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      });

    return () => {
      active = false;
    };
  }, [url, refreshKey]);

  const refetch = useCallback(() => {
    if (!url) return;
    responseCache.delete(url);
    inFlightQueries.delete(url);
    setRefreshKey((value) => value + 1);
  }, [url]);

  return { ...state, refetch };
}
