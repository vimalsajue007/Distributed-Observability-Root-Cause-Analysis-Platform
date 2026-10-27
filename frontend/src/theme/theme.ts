import { alpha, createTheme, type PaletteMode, type Theme } from '@mui/material/styles';

/** Violet design tokens. Dark violet is the default look; a light violet variant is available. */
export const violet = {
  50: '#f5f3ff', 100: '#ede9fe', 200: '#ddd6fe', 300: '#c4b5fd', 400: '#a78bfa', 500: '#8b5cf6', 600: '#7c3aed', 700: '#6d28d9', 800: '#5b21b6', 900: '#4c1d95', 950: '#2e1065',
};

export const chartColors = ['#a78bfa', '#22d3ee', '#f472b6', '#fbbf24', '#34d399', '#fb7185', '#60a5fa', '#c084fc'];

export const statusColors = { ok: '#34d399', warn: '#fbbf24', error: '#f87171', info: '#60a5fa', muted: '#8b7fb0' };

export function createAppTheme(mode: PaletteMode): Theme {
  const dark = mode === 'dark';
  const palette = dark
    ? {
        mode,
        primary: { main: violet[400], light: violet[300], dark: violet[600], contrastText: '#12081f' },
        secondary: { main: '#e879f9', light: '#f0abfc', dark: '#c026d3' },
        background: { default: '#120a22', paper: '#1b1130' },
        text: { primary: '#f3eefc', secondary: '#b9aadb', disabled: '#74679b' },
        divider: alpha(violet[300], 0.16),
        success: { main: '#34d399' }, warning: { main: '#fbbf24' }, error: { main: '#f87171' }, info: { main: '#60a5fa' },
      }
    : {
        mode,
        primary: { main: violet[700], light: violet[500], dark: violet[900], contrastText: '#ffffff' },
        secondary: { main: '#a21caf' },
        background: { default: '#f6f3ff', paper: '#ffffff' },
        text: { primary: '#1f1235', secondary: '#5b4b82', disabled: '#9a8dbd' },
        divider: alpha(violet[800], 0.14),
        success: { main: '#059669' }, warning: { main: '#b45309' }, error: { main: '#dc2626' }, info: { main: '#2563eb' },
      };

  return createTheme({
    palette,
    shape: { borderRadius: 12 },
    typography: {
      fontFamily: '"Inter", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif',
      h4: { fontWeight: 700, letterSpacing: '-0.02em' },
      h5: { fontWeight: 700, letterSpacing: '-0.01em' },
      h6: { fontWeight: 650 },
      subtitle2: { fontWeight: 600 },
      button: { textTransform: 'none', fontWeight: 600 },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundImage: dark
              ? `radial-gradient(1100px 600px at 85% -10%, ${alpha(violet[600], 0.22)}, transparent 60%), radial-gradient(900px 500px at -10% 110%, ${alpha('#c026d3', 0.12)}, transparent 55%)`
              : `radial-gradient(1000px 500px at 90% -10%, ${alpha(violet[300], 0.35)}, transparent 60%)`,
            backgroundAttachment: 'fixed',
          },
          '*::-webkit-scrollbar': { width: 10, height: 10 },
          '*::-webkit-scrollbar-thumb': { background: alpha(violet[400], 0.35), borderRadius: 8 },
          code: { fontFamily: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace', fontSize: '0.85em' },
        },
      },
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: { root: { backgroundImage: 'none', border: `1px solid ${palette.divider}` } },
      },
      MuiCard: { styleOverrides: { root: { backgroundImage: 'none' } } },
      MuiAppBar: { defaultProps: { elevation: 0, color: 'transparent' }, styleOverrides: { root: { backdropFilter: 'blur(12px)', borderBottom: `1px solid ${palette.divider}`, backgroundColor: alpha(palette.background.default, 0.72) } } },
      MuiDrawer: { styleOverrides: { paper: { backgroundImage: 'none', backgroundColor: dark ? '#0f0820' : '#ffffff', borderRight: `1px solid ${palette.divider}` } } },
      MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { containedPrimary: { backgroundImage: `linear-gradient(135deg, ${violet[500]}, ${violet[700]})`, color: '#fff', '&:hover': { backgroundImage: `linear-gradient(135deg, ${violet[400]}, ${violet[600]})` } } } },
      MuiTableCell: { styleOverrides: { head: { fontWeight: 650, color: palette.text.secondary, backgroundColor: alpha(violet[500], dark ? 0.1 : 0.06), whiteSpace: 'nowrap' } } },
      MuiTableRow: { styleOverrides: { root: { '&.MuiTableRow-hover:hover': { backgroundColor: alpha(violet[500], dark ? 0.14 : 0.07) } } } },
      MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: 10,
            '&.Mui-selected': { backgroundColor: alpha(violet[500], dark ? 0.24 : 0.14), color: dark ? violet[200] : violet[800], '&:hover': { backgroundColor: alpha(violet[500], dark ? 0.3 : 0.18) } },
          },
        },
      },
      MuiTooltip: { styleOverrides: { tooltip: { backgroundColor: dark ? '#2a1b4a' : violet[900], fontSize: 12 } } },
    },
  });
}
