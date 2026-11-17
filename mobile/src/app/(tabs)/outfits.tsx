import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, View } from 'react-native';

import { OutfitCard } from '@/components/OutfitCard';
import { Banner, Chip, EmptyState, Page } from '@/components/ui';
import { WornDiary } from '@/components/WornDiary';
import { useSettings } from '@/lib/settings';
import type { Outfit } from '@/lib/types';
import { space } from '@/theme';

type Collection = 'all' | 'liked' | 'worn';

/** Suggested outfits, with two collections: the ones you liked and the ones you wore. */
export default function OutfitsScreen() {
  const { api, ready } = useSettings();
  const [outfits, setOutfits] = useState<Outfit[]>([]);
  const [collection, setCollection] = useState<Collection>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const likedOnly = collection === 'liked';

  const load = useCallback(async () => {
    if (collection === 'worn') return; // the diary loads itself
    try {
      setOutfits((await api.outfits(likedOnly ? true : undefined)).outfits);
      setError('');
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setLoading(false);
    }
  }, [api, collection, likedOnly]);

  useFocusEffect(
    useCallback(() => {
      if (ready) load();
    }, [ready, load]),
  );

  const chips = (
    <View style={styles.filters}>
      <Chip label="All" selected={collection === 'all'} onPress={() => setCollection('all')} />
      <Chip label="Liked" selected={collection === 'liked'} onPress={() => setCollection('liked')} />
      <Chip label="Worn" selected={collection === 'worn'} onPress={() => setCollection('worn')} />
    </View>
  );

  if (collection === 'worn') {
    return (
      <Page>
        <View style={styles.wornHeader}>{chips}</View>
        <WornDiary />
      </Page>
    );
  }

  return (
    <Page>
      <FlatList
        data={outfits}
        keyExtractor={(outfit) => String(outfit.id)}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={loading && outfits.length > 0} onRefresh={load} />}
        ListHeaderComponent={
          <View style={styles.header}>
            {chips}
            {error ? <Banner text={error} onClose={() => setError('')} /> : null}
          </View>
        }
        ListEmptyComponent={
          loading ? undefined : (
            <EmptyState
              icon="heart-outline"
              title={likedOnly ? 'No liked outfits yet' : 'No outfits yet'}
              text={likedOnly ? 'Tap Like on a suggestion to keep it here.' : 'Ask the stylist for an outfit and it will show up here.'}
            />
          )
        }
        renderItem={({ item }) => (
          <OutfitCard
            outfit={item}
            onFeedback={(liked) => {
              if (likedOnly && liked !== true) setOutfits((current) => current.filter((outfit) => outfit.id !== item.id));
            }}
          />
        )}
      />
    </Page>
  );
}

const styles = StyleSheet.create({
  list: { padding: space.lg, gap: space.md },
  header: { gap: space.md },
  filters: { flexDirection: 'row', gap: space.sm },
  wornHeader: { paddingHorizontal: space.lg, paddingTop: space.lg },
});
