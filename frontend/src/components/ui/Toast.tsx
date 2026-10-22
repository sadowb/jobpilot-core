import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Check, CircleAlert, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { IconButton } from './Button';

const ToastContext = createContext<(message: string, tone?: 'success' | 'error') => void>(() => {});
export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{
    message: string;
    id: number;
    tone: 'success' | 'error';
  } | null>(null);
  const serial = useRef(0);
  const notify = useCallback(
    (message: string, tone: 'success' | 'error' = 'success') =>
      setToast({ message, tone, id: ++serial.current }),
    [],
  );
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div
        className="pointer-events-none fixed right-6 bottom-[max(88px,calc(env(safe-area-inset-bottom)+64px))] left-6 z-50 flex justify-end max-[560px]:right-4 max-[560px]:left-4"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {toast && (
          <div
            key={toast.id}
            className="pointer-events-auto flex w-fit max-w-full items-center gap-3 rounded-full border border-line bg-surface/95 px-4 py-2.5 shadow-floating backdrop-blur-md motion-safe:animate-appear"
          >
            <span
              className={cn(
                'grid size-6 shrink-0 place-items-center rounded-full',
                toast.tone === 'error' ? 'bg-danger-soft text-danger' : 'bg-ok-soft text-ok',
              )}
            >
              {toast.tone === 'error' ? (
                <CircleAlert size={14} aria-hidden="true" />
              ) : (
                <Check size={14} aria-hidden="true" />
              )}
            </span>
            <span className="min-w-0 text-[13px] font-medium text-ink">{toast.message}</span>
            <IconButton
              label="Dismiss notification"
              className="size-7"
              onClick={() => setToast(null)}
            >
              <X size={14} aria-hidden="true" />
            </IconButton>
          </div>
        )}
      </div>
    </ToastContext.Provider>
  );
}
