-- 0002_seed_taxonomy.sql — seed the fixed category list (spec §3) and a
-- category-specific field template per category.
--
-- Idempotent: re-running updates display names/ordering and field templates in
-- place rather than duplicating them. Extendable later by inserting more rows
-- into category_field_defs without a redeploy.

insert into public.categories (id, display_name, sort_order)
values ('tshirt',         'T-Shirt',        10),
       ('polo',           'Polo',           20),
       ('shirt',          'Shirt',          30),
       ('blouse',         'Blouse',         40),
       ('tank_top',       'Tank Top',       50),
       ('sweater',        'Sweater',        60),
       ('sweatshirt',     'Sweatshirt',     70),
       ('hoodie',         'Hoodie',         80),
       ('jacket',         'Jacket',         90),
       ('coat',           'Coat',          100),
       ('blazer',         'Blazer',        110),
       ('cardigan',       'Cardigan',      120),
       ('vest',           'Vest',          130),
       ('jeans',          'Jeans',         140),
       ('trousers',       'Trousers',      150),
       ('shorts',         'Shorts',        160),
       ('lower',          'Lower (Joggers / Leggings / Sweatpants)', 170),
       ('skirt',          'Skirt',         180),
       ('dress',          'Dress',         190),
       ('jumpsuit',       'Jumpsuit',      200),
       ('two_piece_suit', 'Two-Piece Suit', 210),
       ('shoes',          'Shoes',         220),
       ('belt',           'Belt',          230),
       ('hat',            'Hat',           240),
       ('scarf',          'Scarf',         250),
       ('tie',            'Tie',           260)
on conflict (id) do update
    set display_name = excluded.display_name,
        sort_order   = excluded.sort_order;

-- Field template. Each row of the VALUES list is
--   (category_ids, field_name, field_type, required, allowed_values, display_order)
-- and is fanned out across every category id listed.
with template (category_ids, field_name, field_type, required, allowed_values, display_order) as (
    values
    -- Applies to every category.
    (array(select id from public.categories),
     'material', 'text', false, null::text[], 100),

    -- Anything with sleeves.
    (array['tshirt','polo','shirt','blouse','sweater','sweatshirt','hoodie','jacket',
           'coat','blazer','cardigan','dress','jumpsuit'],
     'sleeve_length', 'enum', false,
     array['sleeveless','short','three_quarter','long'], 10),

    -- Tops where the neckline is the distinguishing detail.
    (array['tshirt','blouse','sweater','tank_top','dress'],
     'neckline', 'enum', false,
     array['crew','v_neck','scoop','turtleneck','collared','off_shoulder','halter','other'], 20),

    -- Bottoms.
    (array['jeans','trousers','shorts','lower','skirt'],
     'fit', 'enum', false,
     array['skinny','slim','regular','relaxed','loose'], 10),
    (array['jeans','trousers','shorts','lower','skirt'],
     'rise', 'enum', false,
     array['low','mid','high'], 20),
    (array['jeans','trousers','lower'],
     'inseam_inches', 'number', false, null::text[], 30),

    -- Hem length where it changes how the piece reads.
    (array['skirt','dress','coat'],
     'length', 'enum', false,
     array['mini','knee','midi','maxi','ankle','floor'], 30),

    -- Outerwear / accessories that keep weather out.
    (array['jacket','coat','shoes','hat'],
     'waterproof', 'boolean', false, null::text[], 40),
    (array['jacket','coat','hoodie','vest'],
     'closure', 'enum', false,
     array['zip','button','snap','pullover','open'], 50),

    -- Shoes: one enum instead of exploding the category list.
    (array['shoes'],
     'shoe_type', 'enum', true,
     array['sneaker','boot','sandal','formal','loafer','heel','other'], 10),
    (array['shoes'],
     'heel_height', 'enum', false,
     array['flat','low','mid','high'], 20),

    -- Suits.
    (array['two_piece_suit'],
     'suit_pieces', 'enum', false,
     array['jacket_trousers','jacket_skirt'], 10),

    -- Small accessories.
    (array['belt','hat','scarf','tie'],
     'accessory_size', 'text', false, null::text[], 10)
)
insert into public.category_field_defs
    (category_id, field_name, field_type, required, allowed_values, display_order)
select cid, t.field_name, t.field_type, t.required, t.allowed_values, t.display_order
from template t
cross join lateral unnest(t.category_ids) as cid
on conflict (category_id, field_name) do update
    set field_type     = excluded.field_type,
        required       = excluded.required,
        allowed_values = excluded.allowed_values,
        display_order  = excluded.display_order;
