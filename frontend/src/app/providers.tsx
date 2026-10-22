import { Component, type ErrorInfo, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { Button, ui } from '../components/ui';
import { ApiError } from '../api/api';

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Do not log personal application data. */
  }
  render() {
    return this.state.failed ? (
      <section className={ui.state} role="alert">
        <h1 className="text-[28px] leading-[1.2] font-[650] tracking-tight text-balance">
          The dashboard couldn’t render.
        </h1>
        <p className="leading-relaxed">Reload to try again.</p>
        <Button onClick={() => window.location.reload()}>Reload</Button>
      </section>
    ) : (
      this.props.children
    );
  }
}
const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status < 500),
    },
    mutations: { retry: false },
  },
});

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={client}>
        <BrowserRouter>{children}</BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
