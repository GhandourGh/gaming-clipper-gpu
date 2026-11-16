import type { Metadata } from "next";
import { Shell } from "@/components/shell";
import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "./globals.css";

// Pages are never prerendered and cached: Runpod's web address sits behind Cloudflare, which kept serving a page
// prerendered before an update (Cache-Control: s-maxage=1 year), so people ran the old JavaScript after every deploy.
export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Clipper Dashboard", description: "Create, monitor, and review gaming clips." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Shell>{children}</Shell></body></html>;
}
