import { useState } from 'react';
import { Route, Routes } from 'react-router-dom';

import ConnectionStatus from './components/ConnectionStatus';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import Jobs from './pages/Jobs';
import LiveJobs from './pages/LiveJobs';
import Resumes from './pages/Resumes';
import ResumeMatching from './pages/ResumeMatching';

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="min-h-screen lg:flex">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setSidebarOpen((open) => !open)}
                aria-label="Toggle navigation menu"
                aria-expanded={sidebarOpen}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-navy-700 hover:bg-slate-50 lg:hidden"
              >
                <span aria-hidden="true">☰</span>
              </button>
              <p className="text-sm font-medium text-navy-500">MatchWise Console</p>
            </div>
            <ConnectionStatus />
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 lg:px-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/live-jobs" element={<LiveJobs />} />
            <Route path="/resumes" element={<Resumes />} />
            <Route path="/resume-matching" element={<ResumeMatching />} />
          </Routes>
        </main>

        <footer className="border-t border-slate-200 bg-white">
          <div className="mx-auto w-full max-w-6xl px-4 py-4 text-xs text-slate-400 sm:px-6 lg:px-8">
            MatchWise · Expandable job and resume matching recommendation system
          </div>
        </footer>
      </div>
    </div>
  );
}
