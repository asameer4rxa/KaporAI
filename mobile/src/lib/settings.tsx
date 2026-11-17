import AsyncStorage from '@react-native-async-storage/async-storage';
import type { Session } from '@supabase/supabase-js';
import Constants from 'expo-constants';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { createApi } from './api';
import { createCloudApi } from './cloudApi';
import { supabase } from './supabase';
import type { Backend } from './types';

const KEY_URL = 'fashionai.serverUrl';
const KEY_API_KEY = 'fashionai.apiKey';
const KEY_MODE = 'fashionai.mode';

/** cloud = through the private Supabase copy (any network); direct = straight to server.py on the PC. */
export type Mode = 'cloud' | 'direct';

/** false when mobile/.env has no Supabase settings: only the direct connection is offered. */
export const cloudAvailable = supabase !== null;
const cloudApi = supabase ? createCloudApi(supabase) : null;

/**
 * Best guess for where server.py is running:
 * - in a browser: the same machine that serves the page, port 8000
 * - in Expo Go: the PC running "expo start" (Expo tells the app its address), port 8000
 */
export function defaultServerUrl(): string {
  if (Platform.OS === 'web') {
    const host = typeof window !== 'undefined' && window.location.hostname ? window.location.hostname : 'localhost';
    return `http://${host}:8000`;
  }
  const host = (Constants.expoConfig?.hostUri ?? '').split(':')[0];
  return host ? `http://${host}:8000` : 'http://192.168.1.10:8000';
}

type Settings = {
  /** false until the saved settings and the sign-in state have been read from the device */
  ready: boolean;
  mode: Mode;
  setMode: (mode: Mode) => Promise<void>;
  /** the wardrobe, reached whichever way `mode` says */
  api: Backend;

  // cloud
  signedInAs: string | null;
  needsSignIn: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;

  // direct
  serverUrl: string;
  apiKey: string;
  save: (serverUrl: string, apiKey: string) => Promise<void>;
};

const SettingsContext = createContext<Settings | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [mode, setModeState] = useState<Mode>(cloudAvailable ? 'cloud' : 'direct');
  const [session, setSession] = useState<Session | null>(null);
  const [serverUrl, setServerUrl] = useState(defaultServerUrl);
  const [apiKey, setApiKey] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [savedUrl, savedKey, savedMode] = await Promise.all([
          AsyncStorage.getItem(KEY_URL), AsyncStorage.getItem(KEY_API_KEY), AsyncStorage.getItem(KEY_MODE),
        ]);
        if (savedUrl) setServerUrl(savedUrl);
        if (savedKey) setApiKey(savedKey);
        if (savedMode === 'direct' || (savedMode === 'cloud' && cloudAvailable)) setModeState(savedMode);
        if (supabase) setSession((await supabase.auth.getSession()).data.session);
      } finally {
        setReady(true);
      }
    })();
    if (!supabase) return undefined;
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  const setMode = useCallback(async (next: Mode) => {
    if (next === 'cloud' && !cloudAvailable) return;
    setModeState(next);
    await AsyncStorage.setItem(KEY_MODE, next);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) throw new Error('The cloud connection is not set up in this build of the app.');
    const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password });
    if (error) throw new Error(/invalid login/i.test(error.message) ? 'Wrong email or password.' : error.message);
  }, []);

  const signOut = useCallback(async () => {
    await supabase?.auth.signOut();
  }, []);

  const save = useCallback(async (nextUrl: string, nextKey: string) => {
    const cleanUrl = nextUrl.trim().replace(/\/+$/, '') || defaultServerUrl();
    setServerUrl(cleanUrl);
    setApiKey(nextKey.trim());
    await Promise.all([AsyncStorage.setItem(KEY_URL, cleanUrl), AsyncStorage.setItem(KEY_API_KEY, nextKey.trim())]);
  }, []);

  const directApi = useMemo(() => createApi(serverUrl, apiKey), [serverUrl, apiKey]);
  const api: Backend = mode === 'cloud' && cloudApi ? cloudApi : directApi;
  const signedInAs = session?.user.email ?? null;
  const needsSignIn = ready && mode === 'cloud' && !session;

  const value = useMemo(
    () => ({ ready, mode, setMode, api, signedInAs, needsSignIn, signIn, signOut, serverUrl, apiKey, save }),
    [ready, mode, setMode, api, signedInAs, needsSignIn, signIn, signOut, serverUrl, apiKey, save],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): Settings {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error('useSettings must be used inside <SettingsProvider>');
  return settings;
}
