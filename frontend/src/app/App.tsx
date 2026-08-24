import { useConsoleApp } from "@/app/hooks/useConsoleApp";
import { AppShell } from "@/app/AppShell";

export default function App() {
  const vm = useConsoleApp();
  return <AppShell {...vm} />;
}
