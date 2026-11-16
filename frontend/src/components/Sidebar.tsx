import { NavLink } from 'react-router-dom';

interface NavItem {
  to: string;
  label: string;
  hint: string;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', hint: 'System overview' },
  { to: '/jobs', label: 'Jobs', hint: 'Job postings' },
  { to: '/live-jobs', label: 'Live Jobs', hint: 'Live search & import' },
  { to: '/resumes', label: 'Resumes', hint: 'Upload & parse CVs' },
  { to: '/resume-matching', label: 'Resume Matching', hint: 'Candidate–job fit' },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export default function Sidebar({ open, onClose }: SidebarProps) {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    [
      'group flex flex-col gap-0.5 rounded-lg px-3 py-2.5 text-sm transition-colors',
      isActive
        ? 'bg-brand-600 text-white shadow-sm'
        : 'text-navy-700 hover:bg-brand-50 hover:text-brand-600',
    ].join(' ');

  return (
    <>
      {/* Mobile scrim */}
      {open && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-navy-950/40 lg:hidden"
        />
      )}

      <aside
        className={[
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-200 bg-white',
          'transition-transform duration-200 lg:static lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        ].join(' ')}
      >
        <div className="flex items-center gap-3 px-5 py-5">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-navy-900 text-sm font-bold text-white"
          >
            M
          </span>
          <div>
            <p className="text-base font-semibold leading-tight text-navy-900">MatchWise</p>
            <p className="text-xs text-navy-500">Job &amp; Resume Matching</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-2" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={linkClass}>
              <span className="font-medium">{item.label}</span>
              <span
                className={[
                  'text-xs',
                  'text-navy-500 group-hover:text-brand-600/80',
                ].join(' ')}
              >
                {item.hint}
              </span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-200 px-5 py-4">
          <p className="text-xs text-navy-500">Phase 1 · Foundation</p>
        </div>
      </aside>
    </>
  );
}
