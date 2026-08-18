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

## 7. Kiểm chứng

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
