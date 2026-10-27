import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import { ApiError } from '../api/client';
import { AuthProvider } from '../auth/AuthContext';
import { ColorModeProvider } from '../theme/ColorMode';
import { Login } from './Login';

vi.mock('../api/endpoints', () => ({
  api: {
    login: vi.fn(async (u: string, p: string) => {
      if (p !== 'right-password') throw new ApiError({ status: 401, code: 'invalid_credentials', message: 'Invalid username or password', source: 'gateway' });
      return { data: { access_token: 'tok', token_type: 'bearer', expires_in: 3600, user: { username: u, role: 'operator' } }, meta: null, warnings: [], headers: {} };
    }),
    me: vi.fn(),
  },
}));

function setup() {
  return render(
    <ColorModeProvider>
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<div>Dashboard home</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </ColorModeProvider>,
  );
}

describe('Login', () => {
  it('keeps the submit button disabled until both fields are filled', async () => {
    setup();
    const button = screen.getByRole('button', { name: /sign in/i });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/username/i), 'operator');
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/password/i), 'x');
    expect(button).toBeEnabled();
  });

  it('shows the server message for bad credentials and stays on the page', async () => {
    setup();
    await userEvent.type(screen.getByLabelText(/username/i), 'operator');
    await userEvent.type(screen.getByLabelText(/password/i), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password');
    expect(screen.queryByText('Dashboard home')).not.toBeInTheDocument();
  });

  it('signs in and navigates to the dashboard', async () => {
    setup();
    await userEvent.type(screen.getByLabelText(/username/i), 'operator');
    await userEvent.type(screen.getByLabelText(/password/i), 'right-password');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() => expect(screen.getByText('Dashboard home')).toBeInTheDocument());
    expect(sessionStorage.getItem('obs.token')).toBe('tok');
  });
});
