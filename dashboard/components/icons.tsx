type IconProps = { size?: number; className?: string };
const S = ({ size = 18, className, children }: IconProps & { children: React.ReactNode }) => (
  <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);

export const Mark = (p: IconProps) => <S {...p}><path d="M9 7.5v9l7.5-4.5L9 7.5Z" fill="currentColor" stroke="none"/></S>;
export const Home = (p: IconProps) => <S {...p}><path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1v-8Z"/></S>;
export const Queue = (p: IconProps) => <S {...p}><path d="M5 6h14M5 12h14M5 18h9"/></S>;
export const Plus = (p: IconProps) => <S {...p}><path d="M12 5v14M5 12h14"/></S>;
export const Film = (p: IconProps) => <S {...p}><rect x="6" y="3" width="12" height="18" rx="2"/><path d="M6 8h12M6 16h12"/></S>;
export const Send = (p: IconProps) => <S {...p}><path d="M21 3 10 14M21 3l-7 18-4-7-7-4 18-7Z"/></S>;
export const Link = (p: IconProps) => <S {...p}><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></S>;
export const Chart = (p: IconProps) => <S {...p}><path d="M4 20h16M7 16v-5M12 16V7M17 16v-8"/></S>;
export const Gear = (p: IconProps) => <S {...p}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></S>;
export const Menu = (p: IconProps) => <S {...p}><path d="M4 7h16M4 12h16M4 17h16"/></S>;
export const Search = (p: IconProps) => <S {...p}><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></S>;
export const Play = (p: IconProps) => <S {...p}><path d="M8 5.5v13l10.5-6.5L8 5.5Z" fill="currentColor"/></S>;
export const Arrow = (p: IconProps) => <S {...p}><path d="m9 5 7 7-7 7"/></S>;
export const Back = (p: IconProps) => <S {...p}><path d="m15 5-7 7 7 7"/></S>;
export const Check = (p: IconProps) => <S {...p}><path d="m5 12.5 4.2 4L19 7"/></S>;
export const X = (p: IconProps) => <S {...p}><path d="m7 7 10 10M17 7 7 17"/></S>;
export const Download = (p: IconProps) => <S {...p}><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></S>;
export const Refresh = (p: IconProps) => <S {...p}><path d="M19 8a8 8 0 1 0 1 7M19 4v4h-4"/></S>;
export const Upload = (p: IconProps) => <S {...p}><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5h14v-5"/></S>;
export const Trash = (p: IconProps) => <S {...p}><path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/></S>;
export const Calendar = (p: IconProps) => <S {...p}><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></S>;
export const Lock = (p: IconProps) => <S {...p}><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></S>;
export const Sparkle = (p: IconProps) => <S {...p}><path d="m10 3 2 5.5L17.5 10 12 12l-2 5.5L8 12l-5.5-2L8 8.5 10 3Z" fill="currentColor" stroke="none"/><path d="m18.5 15 .9 2.2 2.1.8-2.1.9-.9 2.1-.8-2.1-2.2-.9 2.2-.8.8-2.2Z" fill="currentColor" stroke="none"/></S>;
