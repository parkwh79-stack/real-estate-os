import { HeaderSearch } from "./header-search";
import { HeaderUser } from "./header-user";
import { MobileNavigation } from "./sidebar";

export function Header() {
  return (
    <header className="sticky top-0 z-20 h-14 border-b border-re-border bg-re-surface px-3 sm:px-6 lg:px-8">
      <div className="mx-auto flex h-full w-full max-w-[1600px] items-center gap-2 sm:gap-3">
        <MobileNavigation />
        <div className="min-w-0 flex-1">
          <HeaderSearch />
        </div>

        <HeaderUser />
      </div>
    </header>
  );
}
