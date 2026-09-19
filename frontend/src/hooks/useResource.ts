import { useEffect, useState } from "react";
import { messageOf } from "../components/ui";

/** One server projection per resource. Abort and ownership checks isolate task switches. */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  revision = 0,
) {
  const [state, setState] = useState<{
    data: T | null;
    error: string | null;
    loading: boolean;
  }>({ data: null, error: null, loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState({ data: null, error: null, loading: true });
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted)
          setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({ data: null, error: messageOf(error), loading: false });
      });
    return () => controller.abort();
  }, [load, revision]);
  return state;
}
