import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { SummonStatusProvider } from './SummonStatusContext';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SummonStatusProvider>
      <App />
    </SummonStatusProvider>
  </StrictMode>,
);
