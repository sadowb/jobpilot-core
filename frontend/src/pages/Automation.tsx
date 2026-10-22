import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { api } from '../api/api';
import type { Board } from '../api/models';
import { useAction } from '../hooks/useAction';
import { AutomationSummary } from '../features/automation/AutomationSummary';
import { AutomationControl } from '../features/automation/AutomationControl';
import { Agents } from '../features/automation/Agents';
import { SubmitModeToggle } from '../features/automation/SubmitModeToggle';
import { ui, Segment, Empty, PageHeading, Panel, QueryState } from '../components/ui';
import { Pager, SortHeader, usePagedSort } from '../components/ui/PagedTable';
import { useUrlParams } from '../hooks/useUrlParams';

const policies: { value: Board['automation']; title: string; hint: string }[] = [
  {
    value: 'auto',
    title: 'Auto',
    hint: 'Haiku prepares it; the submit mode below decides who clicks Submit.',
  },
  { value: 'assisted', title: 'Assisted', hint: 'Haiku prepares it; you finish on the site.' },
  { value: 'manual', title: 'Manual', hint: 'You apply yourself; JobPilot only tracks it.' },
];

function BoardRow({ board }: { board: Board }) {
  const save = useAction(
    (next: { policy: Board['automation'] }) => api.saveBoard(board.id, next.policy),
    `${board.displayName} updated.`,
  );

  return (
    <tr className="group hover:bg-raised/40 transition-colors">
      <th scope="row" className={`${ui.th} font-medium text-ink`}>
        {board.displayName}
      </th>
      <td className={ui.td}>
        <div
          role="radiogroup"
          aria-label={`Automation for ${board.displayName}`}
          className={ui.segmented}
          onKeyDown={(event) => {
            // Radio group keyboard contract: arrows move and select, Home/End jump; only the checked option is in the tab order.
            const step = (
              { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>
            )[event.key];
            const at = policies.findIndex((item) => item.value === board.automation);
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? policies.length - 1
                  : step
                    ? (at + step + policies.length) % policies.length
                    : -1;
            if (next < 0 || save.isPending) return;
            event.preventDefault();
            event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=radio]')[next]?.focus();
            if (next !== at)
              save.mutate({
                policy: policies[next].value,
              });
          }}
        >
          {policies.map((item) => (
            <Segment
              key={item.value}
              role="radio"
              aria-checked={board.automation === item.value}
              tabIndex={board.automation === item.value ? 0 : -1}
              title={item.hint}
              aria-disabled={save.isPending || undefined}
              onClick={() => {
                if (!save.isPending && board.automation !== item.value)
                  save.mutate({ policy: item.value });
              }}
              active={board.automation === item.value}
              className="min-h-8 px-3 text-xs"
            >
              {item.title}
            </Segment>
          ))}
        </div>
      </td>
    </tr>
  );
}

function BoardPoliciesTable({ boards }: { boards: Board[] }) {
  const accessors = useMemo(
    () => ({
      board: (row: Board) => row.displayName.toLowerCase(),
      automation: (row: Board) => row.automation,
    }),
    [],
  );
  const url = useUrlParams();
  const paged = usePagedSort(boards, {
    pageSize: 10,
    sort: { key: 'board', dir: 'asc' },
    accessors,
    sortValue: url.get('boards'),
    onSortChange: (value) => url.set({ boards: value }),
  });

  return (
    <>
      <div className={ui.tableScroll} role="region" aria-label="Board policies" tabIndex={0}>
        <table className={ui.table}>
          <thead className="bg-raised/70">
            <tr>
              <SortHeader
                id="board"
                title="Board"
                sort={paged.sort}
                onToggle={paged.toggle}
                className={ui.th}
              />
              <SortHeader
                id="automation"
                title="Automation"
                sort={paged.sort}
                onToggle={paged.toggle}
                className={ui.th}
              />
            </tr>
          </thead>
          <tbody>
            {paged.rows.map((board) => (
              <BoardRow key={board.id} board={board} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="pt-2">
        <Pager
          page={paged.page}
          pages={paged.pages}
          total={paged.total}
          pageSize={paged.pageSize}
          onPage={paged.setPage}
          label="boards"
        />
      </div>
    </>
  );
}

export function Automation() {
  const query = useQuery({ queryKey: ['automation'], queryFn: api.automation });
  const boards = useQuery({ queryKey: ['boards'], queryFn: api.boards });
  const data = query.data;

  return (
    <>
      <PageHeading
        title="Automation"
        description="Run several agents in parallel and set how each board is handled."
        action={data && <AutomationControl running={data.running} />}
      />
      <QueryState
        pending={query.isPending}
        error={query.error}
        retry={() => {
          void query.refetch();
        }}
      >
        {data && (
          <>
            <SubmitModeToggle />
            <AutomationSummary data={data} />
            <Agents data={data} />
          </>
        )}
      </QueryState>

      <div className={ui.notice}>
        <ShieldCheck size={22} />
        <p className="leading-relaxed">
          Email codes can be read from your signed-in webmail through Neo. CAPTCHA and other MFA
          pause for you. Complete those in the browser window and continue via the Needs you inbox.
        </p>
      </div>

      <Panel title="Board policies">
        <QueryState
          pending={boards.isPending}
          error={boards.error}
          retry={() => {
            void boards.refetch();
          }}
        >
          {boards.data?.length ? (
            <BoardPoliciesTable boards={boards.data} />
          ) : (
            <Empty>No boards configured.</Empty>
          )}
        </QueryState>
      </Panel>
    </>
  );
}
