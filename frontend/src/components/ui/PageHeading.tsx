import { type ReactNode } from 'react';

export function PageHeading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="mb-7 flex items-center justify-between gap-6 max-[560px]:mb-5 max-[560px]:flex-col max-[560px]:items-start max-[560px]:gap-4 [&>*:last-child:not(:first-child)]:shrink-0 max-[560px]:[&>div:last-child:not(:first-child)]:w-full">
      <div className="min-w-0">
        <h1 className="font-serif text-[28px] sm:text-[32px] leading-tight font-semibold tracking-tight text-ink text-balance [overflow-wrap:anywhere]">
          {title}
        </h1>
        <p className="mt-1 text-[13px] leading-relaxed text-pretty text-muted">{description}</p>
      </div>
      {action}
    </header>
  );
}
