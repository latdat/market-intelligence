# Hướng dẫn tích hợp cho SWE — Supabase / PostgreSQL

Tài liệu này chốt cách hai hệ thống nối với nhau và mô tả những tệp mới trong thư mục này.
Đọc cùng `README.md` (mô tả gói dữ liệu mô phỏng) và `../docs/DATA_CONTRACTS.md` (hợp đồng dữ liệu).

---

## 1. Quyết định

**PostgreSQL của Supabase trở thành cơ sở dữ liệu duy nhất. MongoDB được loại bỏ hoàn toàn,
kể cả bảng users.**

Cần hiểu chính xác phạm vi của quyết định này:

| | |
|---|---|
| Express server của SWE | **Giữ nguyên.** Supabase thay thế MongoDB, không thay thế backend |
| Auth | **Giữ nguyên JWT tự ký + bcrypt.** KHÔNG dùng Supabase Auth ở giai đoạn này |
| React client | **KHÔNG** gọi thẳng database. Client chỉ nói chuyện với Express, y như hiện tại |
| Mongoose | Gỡ bỏ. Các model mirror dữ liệu DE bị xóa, không phải sửa |

Nếu ai đó hiểu thành "chuyển sang Supabase Auth và cho client gọi thẳng database" thì đó là một
dự án khác hẳn, và sẽ phải viết lại toàn bộ ba màn hình đăng nhập vừa hoàn thành.

---

## 2. Truy cập dữ liệu bằng gì

**Dùng `pg` (hoặc Kysely / Drizzle đặt trên `pg`) qua connection string của Supabase.
KHÔNG dùng `@supabase/supabase-js`.**

Lý do: `supabase-js` gọi qua PostgREST trên HTTP và được thiết kế cho thế giới RLS +
client-side — thế giới mà chúng ta vừa quyết định không dùng. Đổi lại nó không có transaction,
join yếu hơn SQL thật, và hiện tại mọi bảng của DE đều đã `revoke` khỏi `anon`/`authenticated`
nên nó chỉ chạy được nếu cầm `service_role` — điều bị cấm ở mục 3. SWE cũng phải **ghi** bảng của
mình (users, preferences, delivery state), nên cần transaction thật.

### Ba quy tắc bắt buộc

1. **Chỉ chạy phía server.** Không biến nào liên quan tới database được đặt tên `VITE_*`. Vite đóng
   gói mọi biến `VITE_*` vào JavaScript công khai.
2. **Đi qua đúng một seam.** Tạo `server/src/repositories/` và mọi truy vấn Postgres nằm trong đó.
   Không controller nào được import client database trực tiếp. Đây là điều kiện để sau này đổi
   nguồn dữ liệu (thêm cache, hoặc đổi chiến lược đọc) chỉ tốn một file.
3. **KHÔNG dùng `service_role`.** Role đó bypass toàn bộ RLS và hiện có quyền `insert`/`delete`
   trên bảng `articles` của DE. SWE sẽ được cấp một role riêng: `SELECT` trên các bảng của DE,
   toàn quyền trên schema của SWE.

### Phân chia schema

| Schema | Chủ sở hữu | Nội dung |
|---|---|---|
| `public` | DE | `articles`, `article_classifications`, `alert_candidates`, `discovery_*` |
| schema riêng của SWE (ví dụ `app`) | SWE | users, preferences, delivery/notification state |

DE chỉ `SELECT` schema của SWE (để đọc preference cho matching). SWE chỉ `SELECT` schema `public`.
Không bên nào ghi vào bảng của bên kia.

---

## 3. Việc cần làm trước khi kết nối được

Những mục dưới đây đang chặn, tính đến ngày viết tài liệu:

- **DE:** cấp `GRANT SELECT` và tạo role cho SWE. Hiện tại chưa có câu `CREATE POLICY` nào và
  `anon`/`authenticated` đã bị revoke — nghĩa là chưa role nào ngoài `service_role` đọc được gì.
- **DE:** áp hai migration `alert_candidates` (DE-012) lên remote. Chúng mới chỉ chạy trên
  PostgreSQL local.
- **DE:** xác nhận bảng `articles` trên remote đã có dữ liệu thật. Trước bước này mọi thứ khác
  đều là suy đoán.
- **CẢ HAI:** chốt định danh người dùng. Khi users nằm chung một database, `user_id` trở thành
  khóa ngoại thật — nhưng phải ghi rõ kiểu và ai sinh ra nó.

---

## 4. Có thể làm ngay, không cần chờ

Toàn bộ giao diện dựng được ngay hôm nay từ gói dữ liệu trong thư mục này. Khi DE có dữ liệu thật
trên remote, chỉ cần đổi nguồn từ file sang truy vấn — nếu type của cả hai bên sinh từ cùng một
JSON Schema thì bước đổi này gần như không tốn gì.

### Tệp mới trong thư mục này

**Hợp đồng dữ liệu (sinh tự động từ chính model Python của DE — không sửa tay):**

| Tệp | Nội dung |
|---|---|
| `canonical_article.schema.json` | JSON Schema của `CanonicalArticle` |
| `classified_article.schema.json` | JSON Schema của `ClassifiedArticle` |
| `user_preference.schema.json` | JSON Schema của `UserPreference` |
| `alert_candidate.schema.json` | JSON Schema của `AlertCandidate` |
| `source_definition.schema.json` | JSON Schema của `SourceDefinition` |
| `raw_article.schema.json` | JSON Schema của `RawArticle` (nội bộ DE, để tham khảo) |
| `vocabularies.json` | Danh sách chuẩn: 4 markets, 5 categories, 8 topics, 2 importance |

**Dữ liệu mẫu bổ sung:**

| Tệp | Nội dung |
|---|---|
| `sources.sample.json` | **21 nguồn thật** đang cấu hình, kèm cờ rights. Đây là bảng tra cứu cho mục 5 |
| `articles.supplement.sample.json` | 7 bài bổ sung: tiếng Việt, tiếng Trung, tiêu đề dài 190 ký tự, bài từ nguồn bị mute, bài đủ 4 market + 5 topic |
| `article_classifications.supplement.sample.json` | 8 bản ghi, **có một cặp reclassification**: cùng `article_id`, hai `classifier_version` (v1 và v2), cả hai đều được giữ |
| `user_preferences.supplement.sample.json` | 3 user: một người mute nguồn, một người rỗng hoàn toàn (empty state), một người đọc tiếng Việt |
| `alert_candidates.supplement.sample.json` | 2 candidate. Hai user còn lại **cố ý không có candidate nào** |
| `sources.supplement.sample.json` | 3 nguồn mô phỏng, trong đó có một nguồn `can_show_snippet = true` để dựng được nhánh còn lại của mục 5 |

### Sinh type TypeScript

```bash
npx json-schema-to-typescript -i swe_handoff/canonical_article.schema.json -o server/src/contracts/canonicalArticle.ts
```

Làm tương tự cho từng schema, commit kết quả, và **không bao giờ khai lại type của entity dùng
chung bằng tay**. Đọc file JSON mẫu bằng mắt rồi tự khai type chính là cách bộ lỗi hiện tại
xuất hiện.

---

## 5. Bắt buộc: cổng kiểm tra quyền hiển thị

Đây không phải chuyện giao diện, đây là chuyện pháp lý.

**Cả 21 nguồn đang cấu hình đều có `can_show_snippet = false` và
`rights_review_status = "PENDING"`.** Xem `sources.sample.json` để tự kiểm chứng.

Nghĩa là hiện tại **không nguồn nào được phép hiển thị `description` công khai**. Giao diện phải:

1. Join bài viết sang nguồn qua `source_id`.
2. Chỉ render `description` khi `rights.can_show_snippet === true`.
3. Áp dụng đúng luật đó cho cả email, không chỉ web.

`sources.supplement.sample.json` có một nguồn `can_show_snippet = true` để dựng và kiểm thử được
cả hai nhánh. Đừng hardcode nhánh "được phép".

Tương tự: **không tạo cột `content` để lưu toàn văn bài báo.** Cả 21 nguồn đều
`can_store_full_text = false`.

---

## 6. Những điều dễ hiểu sai (rút ra từ đợt rà soát)

| Nhầm lẫn | Thực tế |
|---|---|
| `market` (một giá trị) | `ClassifiedArticle.markets` là **mảng**, tối đa 4. `CanonicalArticle.market` mới là một giá trị — và là market của *nguồn*, không phải của *nội dung*. Hai thứ khác nhau |
| Mỗi bài một classification | Định danh là `(article_id, classifier_version)`. Một bài có thể có nhiều bản, các bản cũ **không bị xóa** |
| `category` luôn có | `category` là `null` khi `is_relevant = false`. Khi đó `markets` và `topics` cũng rỗng |
| `importance` có 4 mức | Chỉ có **`NORMAL`** và **`HIGH`** |
| `match_reasons` có thể rỗng | DE bảo đảm **luôn có ít nhất 1 phần tử**. Không cần xử lý mảng rỗng, nhưng cũng đừng mặc định `[]` |
| `description` rỗng thì để `""` | `null` nghĩa là *không có mô tả*, khác với mô tả rỗng. Giữ nguyên `null` |
| `published_at` thiếu thì lấy giờ crawl | **Tuyệt đối không.** `null` là `null`; dùng `discovered_at` riêng khi cần tính độ mới |
| `language` mặc định `"vi"` | Không có mặc định. Nguồn nào cũng khai ngôn ngữ; gói dữ liệu có cả `en`, `vi`, `zh` |

---

## 7. Kiểu dữ liệu và khoá — DE chốt, SWE làm theo

Mọi quyết định về kiểu dữ liệu, khoá chính, khoá ngoại và ràng buộc của các entity dùng chung
do DE chốt; SWE hiện thực theo. Thấy chỗ nào sai hoặc thiếu thì đề xuất, đừng tự đổi ở phía mình —
một mapping lệch âm thầm chính là nguyên nhân của cả đợt rà soát vừa rồi.

Các quy ước dưới đây đọc trực tiếp từ DDL đang chạy trong `supabase/migrations/`.

### 7.1 Quy ước chung

| Loại dữ liệu | Dùng | Không dùng |
|---|---|---|
| Chuỗi | `text` | `varchar(n)` — không giới hạn độ dài ở tầng DB |
| Mảng | `text[]` | bảng phụ, chuỗi phân tách bằng dấu phẩy |
| Thời gian | `timestamptz`, luôn UTC | `timestamp` trần, `date`, epoch dạng số |
| Số thực | `double precision` + CHECK khoảng giá trị | `numeric`, `real`, `float4` |
| Boolean | `boolean` | `smallint` 0/1, chuỗi `"true"` |
| Tập giá trị cố định | **CHECK constraint** | Postgres `ENUM` type |

Về điểm cuối: thêm topic thứ 9 vào một `ENUM` type cần `ALTER TYPE`, không rollback được trong
transaction và khó đảo ngược. Với CHECK thì đó chỉ là một migration bình thường. DE đang làm vậy
cho `market`, `category`, `topics` và `importance` — SWE làm giống hệt, lấy danh sách giá trị từ
`vocabularies.json`.

### 7.2 `user_id`

- **Kiểu: `text`, không rỗng.** Không phải `uuid`, không phải `bigint`.
- DE khai `alert_candidates.user_id text not null` kèm CHECK `length(btrim(user_id)) > 0`.
- **SWE sinh giá trị; DE coi nó là chuỗi mờ** — không parse, không cast, không giả định định dạng.
- Migrate user cũ từ Mongo: **giữ nguyên chuỗi 24 ký tự hex của `_id`**. JWT hiện tại đã mang đúng
  chuỗi đó nên tầng auth không phải đổi gì.
- Tạo mới từ đầu: UUID v4 lưu dạng `text`.
- Một giá trị, một dạng biểu diễn, ở mọi nơi. Không có chỗ nào lưu dạng khác rồi convert khi join.

### 7.3 Khoá ngoại

**Không có khoá ngoại xuyên schema.** `public.alert_candidates.user_id` **không** tham chiếu bảng
users của SWE, và sẽ không tham chiếu trong giai đoạn này. Nếu có, migration của DE sẽ phụ thuộc
vào bảng của SWE tồn tại, và thứ tự deploy của hai repo bị ghép cứng vào nhau.

> **Hệ quả SWE cần xử lý:** xoá một user **không** cascade. Không ràng buộc nào tự dọn
> `alert_candidates` của user đó. Phải xử lý tường minh — soft-delete user, hoặc gọi thủ tục dọn
> dẹp. Đừng cho rằng database tự lo.

Trong cùng một schema thì dùng FK thoải mái. DE đã có:

```sql
alert_candidates.article_id → articles.article_id  ON DELETE RESTRICT
```

**Chú ý `RESTRICT`:** không xoá được một bài viết khi còn candidate trỏ tới nó. Job dọn dữ liệu
theo retention phải xoá candidate trước, bài viết sau. Làm ngược lại sẽ báo lỗi ràng buộc chứ không
phải lỗi logic, và sẽ mất thời gian truy nguyên.

### 7.4 Bảng ánh xạ kiểu

**CanonicalArticle** — `public.articles`

| Thuộc tính | Postgres | TypeScript | Ghi chú |
|---|---|---|---|
| `article_id` | `text primary key` | `string` | Khoá join. DE sinh, SWE không tính lại |
| `source_id` | `text not null` | `string` | Join sang `sources.sample.json` để kiểm quyền hiển thị |
| `source_item_id` | `text null` | `string \| null` | |
| `url` / `canonical_url` | `text not null` | `string` | `canonical_url` là bản đã bỏ tham số theo dõi |
| `title` | `text not null` | `string` | Không giới hạn độ dài; gói mẫu có tiêu đề 190 ký tự |
| `description` | `text null` | `string \| null` | `null` ≠ `""`. Giữ nguyên `null` |
| `language` | `text not null` | `string` | Không mặc định. Gói mẫu có `en`, `vi`, `zh` |
| `market` | `text not null` + CHECK 4 giá trị | `'VN'\|'US'\|'EU'\|'CN'` | Market của **nguồn**, không phải của nội dung |
| `published_at` | `timestamptz null` | `string \| null` | ISO 8601 UTC. **Không bao giờ đắp giờ crawl vào** |
| `discovered_at` | `timestamptz not null` | `string` | Thời điểm DE phát hiện lần đầu |
| `content_hash` | `text not null` | `string` | DE tính. SWE không tính lại bằng thuật toán khác |

Ngữ nghĩa: bản ghi `articles` là **ghi một lần**. DE chỉ có quyền `select, insert, delete` trên
bảng này, không có `update`. Đừng thiết kế giao diện giả định sửa được nội dung bài.

**ClassifiedArticle** — `public.article_classifications`

| Thuộc tính | Postgres | TypeScript | Ghi chú |
|---|---|---|---|
| `article_id` + `classifier_version` | **khoá chính kép** | | Một bài có thể có nhiều bản phân loại |
| `is_relevant` | `boolean` | `boolean` | Trường phân nhánh. Quyết định hình dạng của 3 dòng dưới — xem 7.4.1 |
| `markets` | `text[]` | `Market[]` | **1–4** phần tử khi relevant (không được rỗng); **bắt buộc rỗng** khi irrelevant |
| `category` | `text null` + CHECK 5 giá trị | `Domain \| null` | **Bắt buộc có** khi relevant; **bắt buộc `null`** khi irrelevant |
| `topics` | `text[]` | `Topic[]` | **0–5** phần tử khi relevant (có thể rỗng); **bắt buộc rỗng** khi irrelevant |
| `confidence` | `double precision` | `number` | Khoảng `[0, 1]` |
| `classified_at` | `timestamptz` | `string` | Thời điểm DE phân loại, **không phải** thời điểm ghi |

#### 7.4.1 Ràng buộc ngữ nghĩa của ClassifiedArticle

Ba trường `markets`, `category`, `topics` **không độc lập với nhau**. Chúng bị ràng buộc theo
`is_relevant` bằng một CHECK trong SQL (`20260816000000_create_article_classifications.sql`,
`article_classifications_semantic_state_check`). Đây là ràng buộc ở tầng database, không phải
quy ước — ghi sai thì insert bị từ chối.

| `is_relevant` | `markets` | `category` | `topics` |
|---|---|---|---|
| `true` | 1–4 phần tử, **không rỗng** | bắt buộc có, 1 trong 5 giá trị | 0–5 phần tử, **có thể rỗng** |
| `false` | **rỗng** | **`null`** | **rỗng** |

Không có tổ hợp thứ ba. Một bài irrelevant kèm `category` là trạng thái không tồn tại được.

Cách encode đúng trong TypeScript là **discriminated union**, để tổ hợp sai không biểu diễn được
ngay từ lúc compile:

```ts
// Sinh từ vocabularies.json — đừng gõ tay
type Market = 'VN' | 'US' | 'EU' | 'CN';
type Domain = 'LAW_POLICY' | 'ENERGY' | 'TECHNOLOGY' | 'REAL_ESTATE' | 'FINANCE';
type Topic =
  | 'AI' | 'BANKING' | 'INTEREST_RATES' | 'OIL_GAS'
  | 'REAL_ESTATE' | 'REGULATION' | 'RENEWABLE_ENERGY' | 'SEMICONDUCTORS';

type ClassificationBase = {
  article_id: string;
  classifier_version: string;  // khớp /^classification-v[1-9][0-9]*$/
  confidence: number;          // [0, 1]
  classified_at: string;       // ISO 8601 UTC
};

export type ClassifiedArticle =
  | (ClassificationBase & {
      is_relevant: true;
      markets: [Market, ...Market[]];  // tối đa 4 — TypeScript không chặn được cận trên
      category: Domain;
      topics: Topic[];                 // tối đa 5
    })
  | (ClassificationBase & {
      is_relevant: false;
      markets: [];
      category: null;
      topics: [];
    });
```

Với kiểu này, `if (c.is_relevant) { c.category }` chạy được mà không cần optional chaining, còn
nhánh `false` thì không truy cập được `category` — đúng như dữ liệu thật.

**Thứ tự phần tử là cố định, không phải alphabet.** DE chuẩn hoá trước khi ghi
(`classification/models.py:114-115`):

- `markets` sắp theo thứ tự cố định **VN → US → EU → CN**. Đây *không phải* thứ tự chữ cái.
- `topics` sắp theo thứ tự chữ cái của giá trị.

Hiển thị theo thứ tự khác thì tuỳ SWE, nhưng **đừng sắp lại rồi so sánh mảng** — và đừng giả định
`markets` đã theo alphabet.

**Lọc theo `status` trước khi đọc.** Bảng `article_classifications` còn chứa các bản ghi đang chạy
dở của pipeline. Khi `status <> 'SUCCEEDED'` thì **toàn bộ** `is_relevant`, `markets`, `category`,
`topics`, `confidence`, `classified_at` đều là `NULL` — cũng do CHECK ở trên ép.

Nghĩa là JSON Schema sinh ra chỉ mô tả **hình dạng của bản ghi đã hoàn tất**, còn bảng thì rộng hơn
thế. Mọi truy vấn của SWE phải có `where status = 'SUCCEEDED'`. Thiếu điều kiện đó, bạn sẽ nhận về
những dòng toàn `null` mà type nói là không thể null.


**AlertCandidate** — `public.alert_candidates`

| Thuộc tính | Postgres | TypeScript | Ghi chú |
|---|---|---|---|
| `candidate_id` | `text primary key` | `string` | DE sinh bằng SHA-256. **Không nối chuỗi `user_id + article_id`** |
| `user_id` | `text not null` | `string` | Xem 7.2 |
| `article_id` | `text not null`, FK RESTRICT | `string` | |
| `matched_at` | `timestamptz not null` | `string` | Thời điểm khớp. Khác thời điểm ghi bản ghi |
| `match_reasons` | `text[] not null` | `string[]` | **Luôn có ít nhất 1 phần tử** |
| `importance` | `text not null` + CHECK | `'NORMAL' \| 'HIGH'` | Chỉ hai giá trị |
| `relevance_score` | `double precision not null` | `number` | `[0, 1]`, loại trừ NaN và Infinity |
| `breaking_eligible` | `boolean not null` | `boolean` | Đủ điều kiện kỹ thuật, không phải lệnh gửi |
| | `unique (user_id, article_id)` | | Một user một bài chỉ một candidate |

### 7.5 Ba điều không được làm

1. **Không đổi tên khi ánh xạ.** `markets` vẫn là `markets` ở mọi tầng — DB, API, TypeScript.
2. **Không thêm `DEFAULT` cho field DE luôn cung cấp.** Một default âm thầm biến "thiếu dữ liệu"
   thành "dữ liệu sai", và không có cách nào phân biệt lại về sau.
3. **Không tự tính lại giá trị DE đã cung cấp** — `content_hash`, `candidate_id`, `relevance_score`,
   `importance`. Lưu đúng giá trị nhận được.

---

## 8. Kiểm chứng

Chạy ở repo `market-intelligence`:

```bash
python scripts/export_contracts.py --check      # báo lỗi nếu schema đã cũ so với model
pytest tests/contract/test_swe_handoff_pack.py  # 16 test, chạy offline, không cần DB
```

Nếu DE đổi model hoặc đổi cấu hình nguồn mà quên sinh lại gói này, lệnh đầu tiên sẽ đỏ.
Đó chính là mục đích của nó.

Lưu ý khi tự viết test: các model dùng chung đặt `strict=True`. Ở chế độ đó `model_validate`
sẽ từ chối một `dict` giải mã từ JSON (mảng JSON là `list` chứ không phải `tuple`, timestamp là
`str` chứ không phải `datetime`). Dùng **`model_validate_json`** cho dữ liệu đọc từ file.
