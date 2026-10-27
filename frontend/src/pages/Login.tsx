import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import { Alert, Box, Button, Paper, Stack, TextField, Typography } from '@mui/material';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { toApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { violet } from '../theme/theme';

export function Login() {
  const { user, login, expired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from ?? '/';

  if (user) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      const apiError = toApiError(err);
      setError(apiError.code === 'too_many_attempts' ? 'Too many failed attempts. Please wait a few minutes and try again.' : apiError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
      <Paper component="form" onSubmit={submit} sx={{ p: { xs: 3, sm: 4.5 }, width: '100%', maxWidth: 420, boxShadow: `0 30px 80px ${violet[900]}66` }}>
        <Stack spacing={2.5}>
          <Stack alignItems="center" spacing={1.25}>
            <Box sx={{ width: 56, height: 56, borderRadius: 3.5, display: 'grid', placeItems: 'center', background: `linear-gradient(135deg, ${violet[400]}, ${violet[700]})` }}>
              <LockOutlinedIcon sx={{ color: '#fff' }} />
            </Box>
            <Typography variant="h5" component="h1">Observability Console</Typography>
            <Typography color="text.secondary" variant="body2" align="center">Sign in to explore traces, logs, metrics and root-cause reports.</Typography>
          </Stack>
          {expired && <Alert severity="info">Your session expired. Please sign in again.</Alert>}
          {error && <Alert severity="error" role="alert">{error}</Alert>}
          <TextField label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required inputProps={{ maxLength: 64 }} />
          <TextField label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required inputProps={{ maxLength: 256 }} />
          <Button type="submit" variant="contained" size="large" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</Button>
        </Stack>
      </Paper>
    </Box>
  );
}
