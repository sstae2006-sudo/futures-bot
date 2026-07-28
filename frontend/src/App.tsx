import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
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
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
