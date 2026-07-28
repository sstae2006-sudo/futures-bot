import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import RequireSession from './components/RequireSession'
import { SessionProvider } from './session'
import MissionControl from './pages/MissionControl'
import Dashboard from './pages/Dashboard'
import BacktestLauncher from './pages/BacktestLauncher'
import TradeExplorer from './pages/TradeExplorer'
import ImportTrades from './pages/ImportTrades'
import Compare from './pages/Compare'
import OptimizerPage from './pages/Optimizer'
import MlResearch from './pages/MlResearch'
import Reports from './pages/Reports'
import Logs from './pages/Logs'
import Jobs from './pages/Jobs'
import MarketRegime from './pages/MarketRegime'
import Experiments from './pages/Experiments'
import Live from './pages/Live'
import MarketData from './pages/MarketData'
import ResearchServer from './pages/ResearchServer'
import Welcome from './pages/Welcome'
import Register from './pages/Register'
import Profile from './pages/Profile'
import OrganizationSettings from './pages/OrganizationSettings'
import TeamMembers from './pages/TeamMembers'

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="welcome" element={<Welcome />} />
          <Route path="register" element={<Register />} />
          <Route element={<RequireSession><Layout /></RequireSession>}>
            <Route index element={<MissionControl />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="backtest" element={<BacktestLauncher />} />
            <Route path="backtest/:runId" element={<BacktestLauncher />} />
            <Route path="trades" element={<TradeExplorer />} />
            <Route path="import" element={<ImportTrades />} />
            <Route path="compare" element={<Compare />} />
            <Route path="optimizer" element={<OptimizerPage />} />
            <Route path="regime" element={<MarketRegime />} />
            <Route path="jobs" element={<Jobs />} />
            <Route path="jobs/:jobId" element={<Jobs />} />
            <Route path="live" element={<Live />} />
            <Route path="market-data" element={<MarketData />} />
            <Route path="research-server" element={<ResearchServer />} />
            <Route path="experiments" element={<Experiments />} />
            <Route path="ml" element={<MlResearch />} />
            <Route path="reports" element={<Reports />} />
            <Route path="logs" element={<Logs />} />
            <Route path="profile" element={<Profile />} />
            <Route path="organization" element={<OrganizationSettings />} />
            <Route path="team" element={<TeamMembers />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  )
}
