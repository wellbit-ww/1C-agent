import type { ReactNode } from "react";

function formatBriefInline(line: string): ReactNode {
  const pieces = line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return pieces.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-semibold text-zinc-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && !part.startsWith("**")) {
      return (
        <em key={index} className="italic text-zinc-200">
          {part.slice(1, -1)}
        </em>
      );
    }
    return part;
  });
}

function BriefBlock({ block }: { block: string }) {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length === 0) return null;

  const [head, ...tail] = lines;
  const bullets = tail.filter((line) => line.startsWith("- "));
  const hasList = bullets.length > 0 && bullets.length === tail.length;

  if (hasList) {
    return (
      <div>
        <p>{formatBriefInline(head)}</p>
        <ul className="mt-2 list-none space-y-1.5 border-l border-line/80 pl-4">
          {bullets.map((line) => (
            <li key={line} className="text-zinc-300">
              {formatBriefInline(line.replace(/^-\s*/, ""))}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (lines.length === 1) {
    return <p>{formatBriefInline(head)}</p>;
  }

  return (
    <div className="space-y-1">
      {lines.map((line) => (
        <p key={line} className={line.startsWith("- ") ? "pl-4 text-zinc-400" : undefined}>
          {formatBriefInline(line.replace(/^-\s*/, ""))}
        </p>
      ))}
    </div>
  );
}

export function BriefSummary({ text }: { text: string }) {
  const blocks = text.split(/\n\n+/).map((block) => block.trim()).filter(Boolean);
  return (
    <div className="mb-3 max-w-3xl space-y-3.5 rounded-xl border border-line bg-card px-4 py-3.5 text-sm leading-relaxed text-zinc-300">
      {blocks.map((block) => (
        <BriefBlock key={block.slice(0, 48)} block={block} />
      ))}
    </div>
  );
}
