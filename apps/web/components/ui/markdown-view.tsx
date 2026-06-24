"use client";

import { Fragment, ReactNode } from "react";

/**
 * Minimal, dependency-free Markdown renderer for the subset our credit documents
 * use: # / ## / ### headings, **bold**, - bullet lists, > blockquote, and
 * | pipe | tables. Renders to styled React elements (no dangerouslySetInnerHTML),
 * so a formal Term Sheet / IC Memo reads as a document, not raw markdown.
 */

function renderInline(text: string, keyBase: string): ReactNode[] {
  // Split on **bold** spans.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={`${keyBase}-b${i}`} className="font-bold text-slate-900 dark:text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <Fragment key={`${keyBase}-t${i}`}>{part}</Fragment>;
  });
}

function isTableRow(line: string): boolean {
  return line.trim().startsWith("|") && line.trim().endsWith("|");
}

function isTableSeparator(line: string): boolean {
  return /^\|[\s:|-]+\|$/.test(line.trim());
}

function splitCells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export function MarkdownView({ markdown, className }: { markdown: string; className?: string }) {
  const lines = (markdown || "").split("\n");
  const at = (n: number): string => lines[n] ?? "";
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = at(i);
    const trimmed = line.trim();

    if (trimmed === "") {
      i += 1;
      continue;
    }

    // Tables: a run of pipe rows (with a separator row).
    if (isTableRow(line) && i + 1 < lines.length && isTableSeparator(at(i + 1))) {
      const header = splitCells(line);
      const rows: string[][] = [];
      i += 2; // skip header + separator
      while (i < lines.length && isTableRow(at(i))) {
        rows.push(splitCells(at(i)));
        i += 1;
      }
      blocks.push(
        <div key={`tbl-${key++}`} className="my-2 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                {header.map((h, hi) => (
                  <th key={hi} className="border border-slate-200 bg-slate-50 px-2 py-1 text-left font-bold dark:border-slate-700 dark:bg-slate-800">
                    {renderInline(h, `th-${key}-${hi}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((cell, ci) => (
                    <td key={ci} className="border border-slate-200 px-2 py-1 align-top dark:border-slate-800">
                      {renderInline(cell, `td-${key}-${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // Headings
    if (trimmed.startsWith("### ")) {
      blocks.push(<h4 key={`h-${key++}`} className="mt-3 text-sm font-bold text-slate-900 dark:text-white">{renderInline(trimmed.slice(4), `h4-${key}`)}</h4>);
      i += 1;
      continue;
    }
    if (trimmed.startsWith("## ")) {
      blocks.push(<h3 key={`h-${key++}`} className="mt-4 border-b border-slate-200 pb-1 text-base font-black text-slate-950 dark:border-slate-700 dark:text-white">{renderInline(trimmed.slice(3), `h3-${key}`)}</h3>);
      i += 1;
      continue;
    }
    if (trimmed.startsWith("# ")) {
      blocks.push(<h2 key={`h-${key++}`} className="mt-1 text-lg font-black text-slate-950 dark:text-white">{renderInline(trimmed.slice(2), `h2-${key}`)}</h2>);
      i += 1;
      continue;
    }

    // Blockquote
    if (trimmed.startsWith("> ")) {
      blocks.push(
        <p key={`bq-${key++}`} className="border-l-2 border-emerald-400 pl-2 text-xs italic text-slate-500 dark:text-slate-400">
          {renderInline(trimmed.slice(2), `bq-${key}`)}
        </p>,
      );
      i += 1;
      continue;
    }

    // Bullet list (run of - / * items)
    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(at(i).trim())) {
        items.push(at(i).trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${key++}`} className="ml-4 list-disc space-y-0.5 text-xs text-slate-700 dark:text-slate-300">
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `li-${key}-${ii}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Paragraph
    blocks.push(
      <p key={`p-${key++}`} className="text-xs text-slate-700 dark:text-slate-300">
        {renderInline(trimmed, `p-${key}`)}
      </p>,
    );
    i += 1;
  }

  return <div className={className ?? "space-y-1"}>{blocks}</div>;
}
