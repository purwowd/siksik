import { useConsoleApp } from "@/app/hooks/useConsoleApp";
import { AppShell } from "@/app/AppShell";
import { DesktopTitleBar } from "@/shared/ui/DesktopTitleBar";

export default function App() {
  const vm = useConsoleApp();
  return (
    <>
      <DesktopTitleBar />
      <AppShell {...vm} />
    </>
  );
}
