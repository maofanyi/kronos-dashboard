import { useCallback, useEffect, useState } from "react";

type PollSnapshot<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
};

type PollEntry = PollSnapshot<unknown> & {
  url: string;
  interval: number;
  subscribers: Set<(snapshot: PollSnapshot<unknown>) => void>;
  timer: number | null;
  inFlight: Promise<void> | null;
  lastStartedAt: number;
  requestSeq: number;
  refCount: number;
};

const pollingEntries = new Map<string, PollEntry>();
const POLLING_DEDUPE_MS = 250;

function snapshotOf<T>(entry: PollEntry): PollSnapshot<T> {
  return {
    data: entry.data as T | null,
    error: entry.error,
    loading: entry.loading,
  };
}

function notifyPollingEntry(entry: PollEntry) {
  const snapshot = snapshotOf(entry);
  entry.subscribers.forEach((subscriber) => subscriber(snapshot));
}

function getPollingEntry(url: string, interval: number) {
  let entry = pollingEntries.get(url);
  if (!entry) {
    entry = {
      url,
      interval,
      data: null,
      error: null,
      loading: true,
      subscribers: new Set(),
      timer: null,
      inFlight: null,
      lastStartedAt: 0,
      requestSeq: 0,
      refCount: 0,
    };
    pollingEntries.set(url, entry);
  } else if (interval < entry.interval) {
    entry.interval = interval;
    if (entry.timer != null) {
      window.clearInterval(entry.timer);
      const pollingEntry = entry;
      entry.timer = window.setInterval(() => {
        void fetchPollingEntry(pollingEntry);
      }, entry.interval);
    }
  }
  return entry;
}

async function fetchPollingEntry(entry: PollEntry, force = false) {
  const now = Date.now();
  if (entry.inFlight) return entry.inFlight;
  if (!force && entry.data != null && now - entry.lastStartedAt < POLLING_DEDUPE_MS) {
    return Promise.resolve();
  }

  entry.lastStartedAt = now;
  const requestSeq = entry.requestSeq + 1;
  entry.requestSeq = requestSeq;
  if (entry.data == null) {
    entry.loading = true;
    notifyPollingEntry(entry);
  }

  entry.inFlight = fetch(entry.url)
    .then(async (resp) => {
      if (!resp.ok) throw new Error(`${resp.status}`);
      const json = await resp.json();
      if (entry.requestSeq !== requestSeq) return;
      entry.data = json;
      entry.error = null;
    })
    .catch((e: unknown) => {
      if (entry.requestSeq !== requestSeq) return;
      entry.error = e instanceof Error ? e.message : String(e);
    })
    .finally(() => {
      if (entry.requestSeq === requestSeq) {
        entry.loading = false;
        entry.inFlight = null;
        notifyPollingEntry(entry);
      }
    });

  return entry.inFlight;
}

function startPollingEntry(entry: PollEntry) {
  void fetchPollingEntry(entry);
  if (entry.timer == null) {
    entry.timer = window.setInterval(() => {
      void fetchPollingEntry(entry);
    }, entry.interval);
  }
}

function subscribePollingEntry<T>(url: string, interval: number, subscriber: (snapshot: PollSnapshot<T>) => void) {
  const entry = getPollingEntry(url, interval);
  const wrappedSubscriber = subscriber as (snapshot: PollSnapshot<unknown>) => void;
  entry.refCount += 1;
  entry.subscribers.add(wrappedSubscriber);
  subscriber(snapshotOf<T>(entry));
  startPollingEntry(entry);

  return () => {
    entry.subscribers.delete(wrappedSubscriber);
    entry.refCount = Math.max(0, entry.refCount - 1);
    if (entry.refCount === 0 && entry.timer != null) {
      window.clearInterval(entry.timer);
      entry.timer = null;
    }
  };
}

export function usePolling<T>(url: string, interval: number = 5000) {
  const [snapshot, setSnapshot] = useState<PollSnapshot<T>>(() => snapshotOf<T>(getPollingEntry(url, interval)));

  useEffect(() => subscribePollingEntry<T>(url, interval, setSnapshot), [url, interval]);

  const refetch = useCallback(async () => {
    const entry = getPollingEntry(url, interval);
    await fetchPollingEntry(entry, true);
  }, [url, interval]);

  return { ...snapshot, refetch };
}
