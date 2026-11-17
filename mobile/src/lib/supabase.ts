import 'react-native-url-polyfill/auto';

import AsyncStorage from '@react-native-async-storage/async-storage';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

// Public values from mobile/.env. The publishable key is safe to ship: on its own it can read
// nothing, because every table and the storage bucket only answer to a signed-in user.
const url = process.env.EXPO_PUBLIC_SUPABASE_URL ?? '';
const key = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? '';

/** null when mobile/.env is not filled in - the app then only offers the direct connection. */
export const supabase: SupabaseClient | null =
  url && key
    ? createClient(url, key, {
        auth: {
          storage: AsyncStorage, // keeps you signed in between app starts
          autoRefreshToken: true,
          persistSession: true,
          detectSessionInUrl: false,
        },
      })
    : null;

export const BUCKET = 'wardrobe';
