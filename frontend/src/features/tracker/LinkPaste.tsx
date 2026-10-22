import { useCallback, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { IconButton, LinkButton, ui } from '../../components/ui';
import { extractLinks, MAX_LINKS, shortLink } from '../../utils/links';

/** Pasted text -> the links to send. Removing a link in the preview hides it until the dialog closes. */
export function useLinkPaste() {
  const [text, setText] = useState('');
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const extracted = useMemo(() => extractLinks(text), [text]);
  const links = useMemo(
    () => extracted.links.filter((link) => !removed.has(link)),
    [extracted, removed],
  );
  const remove = useCallback(
    (link: string) => setRemoved((current) => new Set(current).add(link)),
    [],
  );
  const restore = useCallback(() => setRemoved(new Set()), []);
  return {
    text,
    setText,
    links,
    extracted,
    remove,
    restore,
    removedCount: extracted.links.length - links.length,
  };
}

export type LinkPaste = ReturnType<typeof useLinkPaste>;

const countLabel = (n: number) => `${n} link${n === 1 ? '' : 's'}`;

export function LinkPasteField({
  paste,
  label,
  rows = 4,
  placeholder,
  autoFocus,
  textareaClassName = '',
}: {
  paste: LinkPaste;
  label: string;
  rows?: number;
  placeholder: string;
  autoFocus?: boolean;
  textareaClassName?: string;
}) {
  const { extracted, links } = paste;
  const hasText = paste.text.trim().length > 0;
  return (
    <div className="flex flex-col gap-2">
      <label className={ui.label}>
        <span className="flex items-center justify-between text-xs text-muted">
          <span>{label}</span>
          <span>Paste anything: lines, a spreadsheet column, an email</span>
        </span>
        <textarea
          autoFocus={autoFocus}
          rows={rows}
          value={paste.text}
          onChange={(event) => paste.setText(event.target.value)}
          placeholder={placeholder}
          className={`${ui.textarea} ${textareaClassName}`}
        />
      </label>
      <div className="text-xs text-muted" role="status" aria-live="polite">
        {!hasText
          ? 'Links are found automatically, however they are separated.'
          : links.length
            ? `${countLabel(links.length)} found${extracted.duplicates ? `, ${extracted.duplicates} repeat${extracted.duplicates === 1 ? '' : 's'} merged` : ''}.`
            : 'No links found yet.'}
        {paste.removedCount > 0 && (
          <>
            {' '}
            {paste.removedCount} removed. <LinkButton onClick={paste.restore}>Restore</LinkButton>
          </>
        )}
      </div>
      {extracted.overflow > 0 && (
        <p className="text-xs text-warn" role="alert">
          Only the first {MAX_LINKS} links are used; {extracted.overflow} more were left out. Send
          these first, then paste the rest.
        </p>
      )}
      {links.length > 0 && (
        <ul
          className="max-h-44 list-none divide-y divide-line/60 overflow-y-auto rounded-control border border-line"
          aria-label="Links found"
        >
          {links.map((link) => (
            <li key={link} className="flex items-center justify-between gap-2 px-2.5 py-1">
              <span
                className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink"
                title={link}
              >
                {shortLink(link)}
              </span>
              <IconButton
                label={`Remove ${shortLink(link, 40)}`}
                tone="danger"
                className="size-8"
                onClick={() => paste.remove(link)}
              >
                <X aria-hidden="true" />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
      {extracted.skipped.length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">
            {extracted.skipped.length} skipped (not usable as links)
          </summary>
          <ul className="mt-1 list-none space-y-0.5">
            {extracted.skipped.map((item, index) => (
              <li key={index} className="[overflow-wrap:anywhere]">
                <span className="font-mono">{item.text.slice(0, 80)}</span> — {item.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
