import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import { OutfitCard } from '@/components/OutfitCard';
import { Banner, Button, Chip, Page, SectionTitle, styles as ui } from '@/components/ui';
import { useSettings } from '@/lib/settings';
import type { Preset, Recommendation } from '@/lib/types';
import { colors, space } from '@/theme';

const FALLBACK_PRESETS: Preset[] = [{ name: 'personal', brief: 'Follow your own profile and taste.' }];
const IDEAS = ['Casual day out, 20C', 'Office day', 'Dinner date, cool evening', 'Gym, then coffee'];
const PLANS = ['A normal day', 'Work or classes', 'Going out', 'Staying in', 'Sport'];

export default function StylistScreen() {
  const { api, ready } = useSettings();
  const [occasion, setOccasion] = useState('');
  const [presets, setPresets] = useState<Preset[]>(FALLBACK_PRESETS);
  const [preset, setPreset] = useState('personal');
  const [count, setCount] = useState(2);
  const [think, setThink] = useState(false); // reasoning is opt-in: it costs ~30 s of full GPU load on the PC
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Recommendation | null>(null);

  useEffect(() => {
    if (!ready) return;
    api
      .presets()
      .then((data) => setPresets(data.presets))
      .catch(() => undefined); // the fallback preset still works
  }, [api, ready]);

  const [plan, setPlan] = useState(PLANS[0]);
  const [temperature, setTemperature] = useState('');

  /** One tap for the everyday question: builds the request from today's plan and temperature. */
  function dressMeForToday() {
    const degrees = temperature.trim().replace(/[^0-9.-]/g, '');
    const text = `${plan.toLowerCase()} today${degrees ? `, around ${degrees}C` : ''}`;
    setOccasion(text);
    ask(text);
  }

  async function ask(request?: string) {
    const text = (request ?? occasion).trim();
    if (text.length < 2) {
      setError('Describe the occasion first, e.g. "dinner date, around 15C".');
      return;
    }
    setBusy(true);
    setError('');
    try {
      setResult(await api.recommend({ occasion: text, preset, outfits: count, think }));
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const selected = presets.find((entry) => entry.name === preset);

  return (
    <Page>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.todayTitle}>Today</Text>
        <View style={styles.wrap}>
          {PLANS.map((entry) => (
            <Chip key={entry} label={entry} selected={plan === entry} onPress={() => setPlan(entry)} />
          ))}
        </View>
        <View style={styles.todayRow}>
          <TextInput
            value={temperature}
            onChangeText={setTemperature}
            placeholder="Temp. in C"
            placeholderTextColor={colors.muted}
            keyboardType="numeric"
            style={[ui.input, styles.temperature]}
            accessibilityLabel="Temperature today in Celsius"
          />
          <Button label="Dress me for today" icon="sunny-outline" busy={busy} onPress={dressMeForToday} style={styles.grow} />
        </View>
        <Text style={ui.hint}>Clothes you marked as worn in the last few days are rotated out where possible.</Text>

        <SectionTitle>Or describe the occasion</SectionTitle>
        <Text style={ui.label}>Where are you going? Add the weather if it matters.</Text>
        <TextInput
          value={occasion}
          onChangeText={setOccasion}
          placeholder="e.g. smart casual dinner, around 15C and windy"
          placeholderTextColor={colors.muted}
          multiline
          style={[ui.input, styles.occasion]}
          accessibilityLabel="Occasion"
        />
        <View style={styles.wrap}>
          {IDEAS.map((idea) => (
            <Chip key={idea} label={idea} selected={occasion === idea} onPress={() => setOccasion(idea)} />
          ))}
        </View>

        <SectionTitle>Style</SectionTitle>
        <View style={styles.wrap}>
          {presets.map((entry) => (
            <Chip key={entry.name} label={entry.name} selected={preset === entry.name} onPress={() => setPreset(entry.name)} />
          ))}
        </View>
        {selected ? <Text style={[ui.hint, styles.presetHint]}>{selected.brief}</Text> : null}

        <SectionTitle>Outfits</SectionTitle>
        <View style={styles.wrap}>
          {[1, 2, 3, 4].map((number) => (
            <Chip key={number} label={String(number)} selected={count === number} onPress={() => setCount(number)} />
          ))}
        </View>

        <View style={styles.thinkRow}>
          <View style={styles.thinkText}>
            <Text style={styles.thinkTitle}>Think it through</Text>
            <Text style={ui.hint}>For tricky requests only: about 30 s instead of 5, and it works the PC's GPU hard.</Text>
          </View>
          <Switch value={think} onValueChange={setThink} accessibilityLabel="Think it through" />
        </View>

        <Button
          label={busy ? (think ? 'Thinking - about 30 s ...' : 'Styling ...') : 'Suggest outfits'}
          icon="sparkles-outline"
          busy={busy}
          onPress={() => ask()}
          style={styles.ask}
        />
        {error ? <Banner text={error} onClose={() => setError('')} /> : null}

        {result ? (
          <View style={styles.results}>
            {result.brief ? <Text style={styles.brief}>{result.brief}</Text> : null}
            {result.outfits.map((outfit, index) => (
              <OutfitCard key={outfit.id ?? index} outfit={outfit} />
            ))}
            {result.wardrobe_gap ? <Banner kind="info" text={`Missing from your wardrobe: ${result.wardrobe_gap}`} /> : null}
            <Text style={ui.hint}>
              Answered in {Math.round(result.seconds)} s
              {result.thinking.used ? `, after ${result.thinking.tokens} thinking tokens` : ', without thinking'}. Likes and
              dislikes shape later suggestions.
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </Page>
  );
}

const styles = StyleSheet.create({
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  occasion: { minHeight: 70, textAlignVertical: 'top' },
  todayTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  todayRow: { flexDirection: 'row', gap: space.sm, marginTop: space.md, marginBottom: space.sm },
  temperature: { width: 110 },
  grow: { flex: 1 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.sm },
  presetHint: { marginTop: space.sm },
  thinkRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, marginTop: space.lg },
  thinkText: { flex: 1 },
  thinkTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  ask: { marginTop: space.lg, marginBottom: space.md },
  results: { gap: space.md, marginTop: space.md },
  brief: { color: colors.text, fontSize: 14, lineHeight: 20 },
});
