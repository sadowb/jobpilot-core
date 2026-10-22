import { useState, type MouseEvent } from 'react';
import { ExternalLink, FileText } from 'lucide-react';
import { Dialog } from '../../components/ui/Dialog';
import { Button, IconButton, ui } from '../../components/ui';
import { cvFileName } from '../../utils/humanize';

/** Opens the CV PDF a job (or a track) uses, inside the dashboard. The file is always served as the one public name. */
export function CvPreview({
  job,
  track,
  label = 'Preview CV',
  compact = false,
  onOpenChange,
}: {
  job?: string;
  track?: string;
  label?: string;
  compact?: boolean;
  /** Lets a host (the Peek drawer) step aside while the preview dialog is showing. */
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpenState] = useState(false);
  const setOpen = (next: boolean) => {
    setOpenState(next);
    onOpenChange?.(next);
  };
  const src = job
    ? `/api/jobs/${encodeURIComponent(job)}/cv.pdf`
    : `/api/tracks/${encodeURIComponent(track ?? '')}/cv.pdf`;
  const openPreview = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setOpen(true);
  };
  return (
    <>
      {compact ? (
        <IconButton label="Preview CV" title="Preview the CV" onClick={openPreview}>
          <FileText size={15} aria-hidden="true" />
        </IconButton>
      ) : (
        <Button title="Preview the CV" onClick={openPreview}>
          <FileText size={14} aria-hidden="true" />
          {label}
        </Button>
      )}
      {open && (
        <Dialog title="CV preview" onClose={() => setOpen(false)} wide>
          <p className="!mb-3 text-xs text-muted">Sent to employers as “{cvFileName}”.</p>
          <iframe
            title="CV preview"
            src={`${src}#view=FitH`}
            className="h-[min(70dvh,640px)] w-full rounded-control border border-line bg-raised"
          />
          <div className={ui.dialogActions}>
            <a className={ui.buttonLink} href={src} target="_blank" rel="noopener noreferrer">
              Open in new tab
              <ExternalLink size={13} aria-hidden="true" />
            </a>
            <Button onClick={() => setOpen(false)}>Close</Button>
          </div>
        </Dialog>
      )}
    </>
  );
}
