import { HeaderSearch } from "./header-search";
import { HeaderUser } from "./header-user";
import { MobileNavigation } from "./sidebar";

export function Header() {
  return (
    <header className="sticky top-0 z-20 border-b border-re-border bg-re-surface px-4 py-3 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[1600px] items-center gap-3">
        <MobileNavigation />
        <div className="min-w-0 flex-1">
          <HeaderSearch />
        </div>

        <HeaderUser />
      </div>
    </header>
  );
}
