"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, type ComponentProps } from "react";
import { projectWorkspaceHref } from "../../lib/project-navigation";

type ProjectWorkspaceLinkProps = Omit<ComponentProps<typeof Link>, "href"> & {
  projectId: string;
  path: string;
};

function TrackedLink({ projectId, path, ...props }: ProjectWorkspaceLinkProps) {
  const track = useSearchParams().get("track");
  return <Link {...props} href={projectWorkspaceHref(projectId, path, track)} />;
}

export function ProjectWorkspaceLink({ projectId, path, ...props }: ProjectWorkspaceLinkProps) {
  return (
    <Suspense fallback={<Link {...props} href={projectWorkspaceHref(projectId, path)} />}>
      <TrackedLink {...props} projectId={projectId} path={path} />
    </Suspense>
  );
}
