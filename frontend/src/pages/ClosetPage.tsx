import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage, isApiError } from '../lib/api';
import { sortCategories, useCategories } from '../hooks/useCategories';
import { useMe } from '../hooks/useMe';
import { useTags } from '../hooks/useTags';
import type { Item } from '../lib/types';
import { Banner } from '../components/Banner';
import { ItemCard } from '../components/ItemCard';
import { ItemForm } from '../components/ItemForm';
import { Modal } from '../components/Modal';
import { TagInput } from '../components/TagInput';

const PAGE_SIZE = 24;

type Editing = { mode: 'add' } | { mode: 'edit'; item: Item } | null;

export function ClosetPage() {
  const { categories: rawCategories, error: categoriesError } = useCategories();
  const categories = useMemo(() => sortCategories(rawCategories), [rawCategories]);
  const categoryById = useMemo(
    () => new Map(categories.map((category) => [category.id, category])),
    [categories],
  );
  const { me, refresh: refreshMe } = useMe();
  const { tags: knownTags, refresh: refreshTags } = useTags();

  const [categoryFilter, setCategoryFilter] = useState('');
  const [tagFilters, setTagFilters] = useState<string[]>([]);

  const [items, setItems] = useState<Item[]>([]);
  /** The user's whole closet count — not the number matching the filters. */
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [editing, setEditing] = useState<Editing>(null);

  const reload = useCallback(() => setReloadToken((value) => value + 1), []);
  const filtering = Boolean(categoryFilter) || tagFilters.length > 0;

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .listItems({
        category: categoryFilter || undefined,
        tags: tagFilters.length ? tagFilters : undefined,
        limit: PAGE_SIZE,
        offset: 0,
      })
      .then(
        (page) => {
          if (!active) return;
          setItems(page.items);
          setTotal(page.total);
          setHasMore(page.items.length === PAGE_SIZE);
          setLoading(false);
        },
        (cause: unknown) => {
          if (!active) return;
          setError(errorMessage(cause));
          setLoading(false);
        },
      );
    return () => {
      active = false;
    };
  }, [categoryFilter, tagFilters, reloadToken]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const page = await api.listItems({
        category: categoryFilter || undefined,
        tags: tagFilters.length ? tagFilters : undefined,
        limit: PAGE_SIZE,
        offset: items.length,
      });
      setItems((current) => [...current, ...page.items]);
      setTotal(page.total);
      setHasMore(page.items.length === PAGE_SIZE);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoadingMore(false);
    }
  };

  /**
   * The user's whole tag vocabulary when the backend can supply it; otherwise
   * the tags on the items currently loaded, which is all a paginated list can
   * tell us.
   */
  const tagSuggestions = useMemo(
    () => knownTags ?? [...new Set(items.flatMap((item) => item.tags))].sort(),
    [knownTags, items],
  );

  const deleteItem = async (item: Item) => {
    try {
      await api.deleteItem(item.id);
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      setTotal((current) => Math.max(0, current - 1));
      setNotice('Item deleted.');
      refreshMe();
      // A tag drops out of the vocabulary when its last item goes.
      refreshTags();
    } catch (cause) {
      // A 404 means it is already gone — treat that as success, not an error.
      if (isApiError(cause) && cause.code === 'not_found') {
        setItems((current) => current.filter((entry) => entry.id !== item.id));
        return;
      }
      setError(errorMessage(cause));
    }
  };

  const atCapacity = me !== null && total >= me.item_limit;

  return (
    <div className="page closet-page">
      <div className="page__header">
        <div>
          <h1>Your closet</h1>
          <p className="page__subtitle">
            {total === 0
              ? 'Nothing in here yet.'
              : `${total} ${total === 1 ? 'item' : 'items'}${me ? ` of ${me.item_limit}` : ''}`}
            {filtering && items.length !== total && ` · ${items.length} shown`}
          </p>
        </div>
        <button
          type="button"
          className="button button--primary"
          disabled={atCapacity}
          title={atCapacity ? 'Your closet is full — delete something first.' : undefined}
          onClick={() => setEditing({ mode: 'add' })}
        >
          Add an item
        </button>
      </div>

      {categoriesError && (
        <Banner>Could not load the category list, so the add form is unavailable. {categoriesError}</Banner>
      )}
      {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && (
        <Banner tone="success" onDismiss={() => setNotice(null)}>
          {notice}
        </Banner>
      )}
      {atCapacity && (
        <Banner tone="info">
          Your closet is at its {me?.item_limit}-item limit. Delete something to make room.
        </Banner>
      )}

      <section className="filters" aria-label="Filters">
        <label className="field field--inline">
          <span className="field__label">Category</span>
          <select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
            <option value="">All categories</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.display_name}
              </option>
            ))}
          </select>
        </label>

        <div className="field field--inline filters__tags">
          <span className="field__label">Tags</span>
          <TagInput
            value={tagFilters}
            onChange={setTagFilters}
            suggestions={tagSuggestions}
            placeholder="Filter by tag…"
          />
          <span className="field__hint">An item must have every tag listed.</span>
        </div>

        {filtering && (
          <button
            type="button"
            className="button button--quiet"
            onClick={() => {
              setCategoryFilter('');
              setTagFilters([]);
            }}
          >
            Clear filters
          </button>
        )}
      </section>

      {loading ? (
        <p className="placeholder">Loading your closet…</p>
      ) : items.length === 0 ? (
        <EmptyState
          filtering={filtering}
          onAdd={() => setEditing({ mode: 'add' })}
          canAdd={categories.length > 0 && !atCapacity}
        />
      ) : (
        <>
          <div className="item-grid">
            {items.map((item) => (
              <ItemCard
                key={item.id}
                item={item}
                category={categoryById.get(item.category)}
                onEdit={() => setEditing({ mode: 'edit', item })}
                onDelete={() => deleteItem(item)}
              />
            ))}
          </div>
          {hasMore && (
            <div className="load-more">
              <button
                type="button"
                className="button button--quiet"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}

      {editing && (
        <Modal
          title={editing.mode === 'add' ? 'Add an item' : 'Edit item'}
          onClose={() => setEditing(null)}
        >
          <ItemForm
            categories={categories}
            item={editing.mode === 'edit' ? editing.item : null}
            tagSuggestions={tagSuggestions}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              setNotice(editing.mode === 'add' ? 'Item added.' : 'Changes saved.');
              // Refetch rather than patch the list in place: an edit can move an
              // item out of the current category/tag filter.
              reload();
              refreshMe();
              refreshTags();
            }}
          />
        </Modal>
      )}
    </div>
  );
}

function EmptyState({
  filtering,
  onAdd,
  canAdd,
}: {
  filtering: boolean;
  onAdd: () => void;
  canAdd: boolean;
}) {
  if (filtering) {
    return (
      <div className="placeholder placeholder--empty">
        <p>Nothing matches those filters.</p>
      </div>
    );
  }
  return (
    <div className="placeholder placeholder--empty">
      <h2>Start with what you wear most</h2>
      <p>
        Add a handful of things by hand to get going — a few tops, a pair of jeans, the shoes you
        actually reach for. After that, <Link to="/connect">connect your assistant</Link> and the
        rest can go in by chat.
      </p>
      {canAdd && (
        <button type="button" className="button button--primary" onClick={onAdd}>
          Add your first item
        </button>
      )}
    </div>
  );
}
