import AccountTreeIcon from '@mui/icons-material/AccountTree';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import DashboardIcon from '@mui/icons-material/Dashboard';
import InsightsIcon from '@mui/icons-material/Insights';
import LightModeIcon from '@mui/icons-material/LightMode';
import LogoutIcon from '@mui/icons-material/Logout';
import MenuIcon from '@mui/icons-material/Menu';
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive';
import ScienceIcon from '@mui/icons-material/Science';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import SubjectIcon from '@mui/icons-material/Subject';
import TimelineIcon from '@mui/icons-material/Timeline';
import {
  AppBar, Avatar, Box, Chip, Drawer, FormControl, IconButton, List, ListItemButton, ListItemIcon, ListItemText, MenuItem, Select, Stack, Switch, Toolbar, Tooltip, Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useColorMode } from '../theme/ColorMode';
import { violet } from '../theme/theme';
import { WINDOWS } from '../utils/format';
import { useRefresh } from './RefreshContext';

const DRAWER = 244;

export const NAV = [
  { to: '/', label: 'Overview', icon: <DashboardIcon />, end: true },
  { to: '/traces', label: 'Trace Explorer', icon: <TimelineIcon /> },
  { to: '/dependencies', label: 'Dependency Map', icon: <AccountTreeIcon /> },
  { to: '/logs', label: 'Log Explorer', icon: <SubjectIcon /> },
  { to: '/metrics', label: 'Metrics', icon: <ShowChartIcon /> },
  { to: '/root-cause', label: 'Root-Cause', icon: <InsightsIcon /> },
  { to: '/alerts', label: 'Alert Center', icon: <NotificationsActiveIcon /> },
  { to: '/scenarios', label: 'Scenario Lab', icon: <ScienceIcon /> },
];

function Brand() {
  return (
    <Stack direction="row" spacing={1.25} alignItems="center" sx={{ px: 2.5, py: 2.25 }}>
      <Box sx={{ width: 34, height: 34, borderRadius: 2.5, display: 'grid', placeItems: 'center', background: `linear-gradient(135deg, ${violet[400]}, ${violet[700]})`, boxShadow: `0 6px 18px ${violet[700]}66` }}>
        <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden="true"><path d="M3 18h6l4-11 6 18 4-11h6" fill="none" stroke="#fff" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </Box>
      <Box>
        <Typography fontWeight={750} lineHeight={1.1}>Observability</Typography>
        <Typography variant="caption" color="text.secondary">Root-cause console</Typography>
      </Box>
    </Stack>
  );
}

export function Layout() {
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down('md'));
  const [open, setOpen] = useState(false);
  const { user, logout } = useAuth();
  const { mode, toggle } = useColorMode();
  const { windowMinutes, setWindowMinutes, autoRefresh, setAutoRefresh, refreshNow } = useRefresh();
  const location = useLocation();

  const nav = (
    <Box role="navigation" aria-label="Main">
      <Brand />
      <List sx={{ px: 1.5 }}>
        {NAV.map((item) => (
          <ListItemButton
            key={item.to}
            component={NavLink}
            to={item.to}
            end={item.end}
            selected={item.end ? location.pathname === '/' : location.pathname.startsWith(item.to)}
            onClick={() => setOpen(false)}
            sx={{ mb: 0.5 }}
          >
            <ListItemIcon sx={{ minWidth: 38, color: 'inherit' }}>{item.icon}</ListItemIcon>
            <ListItemText primary={item.label} primaryTypographyProps={{ fontWeight: 600, fontSize: 14.5 }} />
          </ListItemButton>
        ))}
      </List>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <Drawer
        variant={mobile ? 'temporary' : 'permanent'}
        open={mobile ? open : true}
        onClose={() => setOpen(false)}
        ModalProps={{ keepMounted: true }}
        sx={{ width: DRAWER, flexShrink: 0, '& .MuiDrawer-paper': { width: DRAWER, boxSizing: 'border-box' } }}
      >
        {nav}
      </Drawer>
      <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <AppBar position="sticky" color="transparent">
          <Toolbar sx={{ gap: 1.5, flexWrap: 'wrap', py: 0.75 }}>
            {mobile && <IconButton aria-label="Open navigation" edge="start" onClick={() => setOpen(true)}><MenuIcon /></IconButton>}
            <Box sx={{ flexGrow: 1 }} />
            <FormControl size="small" sx={{ minWidth: 170 }}>
              <Select value={windowMinutes} onChange={(e) => setWindowMinutes(Number(e.target.value))} inputProps={{ 'aria-label': 'Time window' }}>
                {WINDOWS.map((w) => <MenuItem key={w.minutes} value={w.minutes}>{w.label}</MenuItem>)}
              </Select>
            </FormControl>
            <Tooltip title="Refresh now"><IconButton aria-label="Refresh now" onClick={refreshNow}><AutorenewIcon /></IconButton></Tooltip>
            <Stack direction="row" alignItems="center" spacing={0.25}>
              <Typography variant="caption" color="text.secondary">Auto</Typography>
              <Switch size="small" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} inputProps={{ 'aria-label': 'Auto refresh every 15 seconds' }} />
            </Stack>
            <Tooltip title={mode === 'dark' ? 'Switch to light violet' : 'Switch to dark violet'}>
              <IconButton aria-label="Toggle colour mode" onClick={toggle}>{mode === 'dark' ? <LightModeIcon /> : <DarkModeIcon />}</IconButton>
            </Tooltip>
            {user && (
              <Stack direction="row" spacing={1} alignItems="center">
                <Chip size="small" color="primary" variant="outlined" label={user.role} />
                <Avatar sx={{ width: 30, height: 30, bgcolor: 'primary.main', color: 'primary.contrastText', fontSize: 14 }}>{user.username.slice(0, 1).toUpperCase()}</Avatar>
                <Typography variant="body2" sx={{ display: { xs: 'none', sm: 'block' } }}>{user.username}</Typography>
                <Tooltip title="Sign out"><IconButton aria-label="Sign out" onClick={logout}><LogoutIcon /></IconButton></Tooltip>
              </Stack>
            )}
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ p: { xs: 2, md: 3.5 }, maxWidth: 1600, width: '100%', mx: 'auto' }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}
