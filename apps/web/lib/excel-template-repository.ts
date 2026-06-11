import { createHash } from "node:crypto";
import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";

export type WorkbookTemplateMetadata = {
  templateMode: "sample_package" | "fallback_mirror";
  templateId: string;
  templateVersion: string;
  sourceType: string;
  sourcePath: string | null;
  sourceFileName: string | null;
  sourceSha256: string | null;
  copiedSheetCount: number;
  copiedSheetNames: string[];
  resolvedAt: string;
};

export type WorkbookTemplateDescriptor = {
  sourcePath: string;
  sourceType:
    | "controlled_template_path"
    | "controlled_template_dir"
    | "local_pilot_override"
    | "local_sample_discovery";
  templateId: string;
  templateVersion: string;
  sourceFileName: string;
  sourceSha256: string;
};

async function pathExists(filePath: string) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function fileSha256(filePath: string) {
  const content = await readFile(filePath);
  return createHash("sha256").update(content).digest("hex");
}

function templateSearchDirectories() {
  return [
    process.env.REALYTICS_EXCEL_TEMPLATE_DIR,
    path.join(/* turbopackIgnore: true */ process.cwd(), "..", "sample"),
    path.join(/* turbopackIgnore: true */ process.cwd(), "sample"),
    path.join(/* turbopackIgnore: true */ process.cwd(), "..", "..", "sample"),
    path.join(/* turbopackIgnore: true */ process.cwd(), "..", "..", "..", "sample"),
  ].filter((directory): directory is string => Boolean(directory));
}

function isCandidateWorkbook(fileName: string) {
  return (
    fileName.toLowerCase().endsWith(".xlsx") &&
    fileName.includes("PF") &&
    fileName.includes("사업수지")
  );
}

async function descriptor(
  sourcePath: string,
  sourceType: WorkbookTemplateDescriptor["sourceType"],
  templateId: string,
  templateVersion: string,
): Promise<WorkbookTemplateDescriptor> {
  return {
    sourcePath,
    sourceType,
    templateId,
    templateVersion,
    sourceFileName: path.basename(sourcePath),
    sourceSha256: await fileSha256(sourcePath),
  };
}

export async function resolveExcelWorkbookTemplate(): Promise<WorkbookTemplateDescriptor | null> {
  if (process.env.REALYTICS_EXCEL_TEMPLATE_PATH) {
    return descriptor(
      process.env.REALYTICS_EXCEL_TEMPLATE_PATH,
      "controlled_template_path",
      process.env.REALYTICS_EXCEL_TEMPLATE_ID || "bonri-pf-feasibility-sample-package",
      process.env.REALYTICS_EXCEL_TEMPLATE_VERSION || "v1-local",
    );
  }

  if (process.env.REALYTICS_SAMPLE_WORKBOOK_PATH) {
    return descriptor(
      process.env.REALYTICS_SAMPLE_WORKBOOK_PATH,
      "local_pilot_override",
      "bonri-pf-feasibility-sample-package",
      "v1-local-sample-override",
    );
  }

  for (const directory of templateSearchDirectories()) {
    if (!(await pathExists(directory))) {
      continue;
    }

    const files = await readdir(directory);
    const workbookFile = files.find(isCandidateWorkbook);

    if (workbookFile) {
      return descriptor(
        path.join(directory, workbookFile),
        directory === process.env.REALYTICS_EXCEL_TEMPLATE_DIR
          ? "controlled_template_dir"
          : "local_sample_discovery",
        process.env.REALYTICS_EXCEL_TEMPLATE_ID || "bonri-pf-feasibility-sample-package",
        process.env.REALYTICS_EXCEL_TEMPLATE_VERSION || "v1-local-discovered",
      );
    }
  }

  return null;
}
