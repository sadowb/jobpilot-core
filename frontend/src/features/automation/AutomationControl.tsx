import { api } from '../../api/api';
import { useAction } from '../../hooks/useAction';
import { Button, ErrorMessage } from '../../components/ui';
export function AutomationControl({
  running,
  subtle = false,
}: {
  running: boolean;
  subtle?: boolean;
}) {
  const action = useAction(
    () => api.toggleAutomation(running),
    running
      ? 'Automation paused. Active jobs will finish their current step.'
      : 'Automation started.',
  );
  return (
    <div>
      <Button
        variant={subtle ? 'secondary' : running ? 'default' : 'primary'}
        loading={action.isPending}
        disabled={action.isPending}
        onClick={() => action.mutate()}
      >
        {action.isPending ? 'Updating…' : running ? 'Stop automation' : 'Start automation'}
      </Button>
      <ErrorMessage error={action.error} />
    </div>
  );
}
