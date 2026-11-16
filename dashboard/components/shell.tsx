"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Chart, Film, Gear, Home, Link as LinkIcon, Mark, Menu, Play, Plus, Queue, Send, Sparkle, X } from "./icons";
import { useData } from "@/lib/api";

type Overview = { review: unknown[]; live: unknown[]; health: Record<string, { ok: boolean; detail: string }> };

const MAIN = [
  { href: "/", label: "Overview", icon: Home },
  { href: "/new", label: "New job", icon: Plus },
  { href: "/jobs", label: "Jobs", icon: Queue },
  { href: "/clips", label: "Clip library", icon: Film },
  { href: "/tools", label: "Tools", icon: Sparkle },
  { href: "/live", label: "Live", icon: Play },
];
const PUBLISH = [
  { href: "/publish", label: "Publishing", icon: Send },
  { href: "/accounts", label: "Accounts", icon: LinkIcon },
];
const SYSTEM = [
  { href: "/gpu", label: "GPU", icon: Sparkle },
  { href: "/costs", label: "Costs & usage", icon: Chart },
  { href: "/settings", label: "Settings", icon: Gear },
];

function active(pathname: string, href: string) { return href === "/" ? pathname === "/" : pathname.startsWith(href); }

function NavLinks({ pathname, counts, onNavigate }: { pathname: string; counts: { review: number; live: number }; onNavigate?: () => void }) {
  const item = (l: typeof MAIN[number]) => (
    <Link key={l.href} href={l.href} className="nav-link" aria-current={active(pathname, l.href) ? "page" : undefined} onClick={onNavigate}>
      <l.icon/><span>{l.label}</span>
      {l.href === "/clips" && counts.review > 0 && <span className="badge accent plain">{counts.review}</span>}
      {l.href === "/jobs" && counts.live > 0 && <span className="badge info live">{counts.live}</span>}
    </Link>
  );
  return <>
    <nav className="nav" aria-label="Create and review">{MAIN.map(item)}</nav>
    <nav className="nav" aria-label="Publish"><div className="nav-label">Publish</div>{PUBLISH.map(item)}</nav>
    <nav className="nav" aria-label="System"><div className="nav-label">System</div>{SYSTEM.map(item)}</nav>
  </>;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [menu, setMenu] = useState(false);
  const login = pathname === "/login";
  const { data } = useData<Overview>(login ? null : "dashboard/overview", 15000);
  const counts = { review: data?.review.length ?? 0, live: data?.live.length ?? 0 };
  const health = data ? Object.values(data.health) : [];
  const healthy = health.length > 0 && health.every(h => h.ok);
  useEffect(() => setMenu(false), [pathname]);

  if (login) return <main style={{ minHeight: "100vh", padding: "1rem" }}>{children}</main>;
  return <div className="app">
    <aside className="sidebar">
      <Link href="/" className="brand"><span className="brand-mark"><Mark size={18}/></span><span>Clipper<small>Gaming clip studio</small></span></Link>
      <NavLinks pathname={pathname} counts={counts}/>
      <div className="sidebar-foot">
        <Link href="/settings" className="health-mini" title={health.map(h => h.detail).join(" · ")}>
          <span className={`dot ${healthy || !data ? "" : "bad"}`}/>{!data ? "Checking system…" : healthy ? "All systems running" : "Something needs attention"}
        </Link>
      </div>
    </aside>

    <header className="topbar">
      <Link href="/" className="brand"><span className="brand-mark"><Mark size={18}/></span><span>Clipper</span></Link>
      <span className={`dot ${healthy || !data ? "" : "bad"}`} title={healthy ? "All systems running" : "Something needs attention"}/>
    </header>

    <main className="main">{children}</main>

    <nav className="tabbar" aria-label="Main">
      <Link href="/" className="tab" aria-current={pathname === "/" ? "page" : undefined}><Home size={20}/><span>Home</span></Link>
      <Link href="/jobs" className="tab" aria-current={active(pathname, "/jobs") ? "page" : undefined}><Queue size={20}/><span>Jobs</span></Link>
      <Link href="/new" className="tab tab-new" aria-label="New job"><span><Plus size={20}/></span><span>New</span></Link>
      <Link href="/clips" className="tab" aria-current={active(pathname, "/clips") ? "page" : undefined}><Film size={20}/><span>Clips</span></Link>
      <button className="tab" onClick={() => setMenu(true)} aria-expanded={menu}><Menu size={20}/><span>More</span></button>
    </nav>
    <div className="menu-sheet" hidden={!menu} onClick={() => setMenu(false)}>
      <div className="menu-panel" onClick={e => e.stopPropagation()} role="dialog" aria-label="Menu">
        <div className="card-title"><h2>Menu</h2><button className="btn btn-ghost icon-btn" onClick={() => setMenu(false)} aria-label="Close menu"><X/></button></div>
        <NavLinks pathname={pathname} counts={counts} onNavigate={() => setMenu(false)}/>
      </div>
    </div>
  </div>;
}
