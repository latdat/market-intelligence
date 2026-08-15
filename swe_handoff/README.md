# SWE Data Ready v1 — Gói Hợp đồng Dữ liệu Mô phỏng (Synthetic Contract Pack)

Thư mục này chứa các ví dụ synthetic (mô phỏng) được liên kết với nhau dành cho SWE phát triển giao diện đọc sau DE-012. Đây là tài liệu về contract (hợp đồng dữ liệu), không phải là bản dump cơ sở dữ liệu, seed cho migration, fixture cho production, và cũng không khẳng định rằng các dòng dữ liệu này tồn tại trên remote database.

## Danh sách Tệp (Files)

Các tệp dữ liệu mô phỏng dưới đây cung cấp đầu vào cho giao diện (UI) ở các giai đoạn hiển thị khác nhau:

- `articles.sample.json` (Dữ liệu bài viết gốc):
  - **Mô tả:** Chứa 35 bản ghi `CanonicalArticle` đại diện cho các bài báo, tin tức vừa được lấy về từ nguồn.
  - **Dành cho SWE:** Dùng để xây dựng giao diện danh sách bài viết thô (raw feed), hiển thị tiêu đề, URL, ngày xuất bản (`published_at`) và mô tả (`description`).

- `article_classifications.sample.json` (Dữ liệu đã qua AI phân loại):
  - **Mô tả:** Chứa 35 bản ghi `ClassifiedArticle` (kết nối với bài viết gốc qua `article_id`) đã được gán nhãn thị trường (`markets`), phân loại (`category`), chủ đề (`topics`) và mức độ liên quan (`is_relevant`).
  - **Dành cho SWE:** Dùng để xây dựng các bộ lọc (filters) trên UI (ví dụ: lọc theo chủ đề AI, thị trường US/EU) và các tag trạng thái. File này cố ý loại trừ vòng đời persistence, claim token, retry, cost, và metadata của provider.

- `user_preferences.sample.json` (Dữ liệu cài đặt người dùng):
  - **Mô tả:** Chứa 12 bản ghi `UserPreference` quy định luật nhận thông báo của user (thị trường theo dõi, chủ đề bị ẩn, ngưỡng quan trọng). Các bản ghi nằm dưới key `records`.
  - **Dành cho SWE:** Dùng làm dữ liệu mock cho màn hình Cài đặt cá nhân (Settings/Preferences) để người dùng có thể tùy chỉnh luồng thông báo.

- `alert_candidates.sample.json` (Dữ liệu ứng viên cảnh báo/thông báo):
  - **Mô tả:** Chứa các bản ghi `AlertCandidate` là kết quả đối chiếu (matching) hợp lệ giữa bài viết đã phân loại và cài đặt của người dùng.
  - **Dành cho SWE:** Đây là dữ liệu quan trọng nhất để render danh sách thông báo đẩy (push notifications) hoặc in-app alerts (news feed cá nhân hóa). Cần chú ý các trường như `breaking_eligible`, `importance_score` để làm nổi bật cảnh báo. Bài viết không liên quan (irrelevant) cố ý không có candidate nào.

## Mối quan hệ và phạm vi bao phủ (Relationships and coverage)

Kết nối (join) bài viết với classifications và candidates qua `article_id`. Kết nối preferences với candidates qua `user_id`. Mọi định danh ngoại lai (foreign identifier) trong gói sample này đều có thể phân giải (resolve) bên trong thư mục này.

Các bản ghi bao phủ các thị trường VN, US, EU, và CN; toàn bộ năm phân mục (categories) hiện tại; toàn bộ tám chủ đề (topics) được kiểm soát; ngữ nghĩa classification hợp lệ (relevant) và không hợp lệ (irrelevant); các trường nullable như `source_item_id`, `description`, `published_at`, và `category`; các ứng viên khẩn cấp (breaking) và thông thường (non-breaking); và các tổ hợp cờ thông báo (notification flags) khác nhau.

Tất cả tiêu đề, mô tả, URL, ID, người dùng, timestamps, và các dòng candidate đều là synthetic.
Không bao gồm toàn văn bài viết từ nhà xuất bản hay bất kỳ đoạn trích dẫn có bản quyền nào.

## Kịch bản thử nghiệm (Demo scenarios)

Các kịch bản sau cung cấp sự kết hợp dữ liệu thực tế (realistic) để kiểm thử các trạng thái UI trên frontend:

- **Kịch bản A: Cảnh báo khẩn cấp ưu tiên cao về quy định AI (High-priority breaking AI regulation alert)**
  Bài viết `2de7b5fdb9eb0387f6840ddd3cea3a9996a82137391de207ecf2d5badd1d96ce` bao phủ các thị trường US và CN và sinh ra một candidate có mức độ quan trọng `HIGH` với `breaking_eligible=true` cho người dùng theo dõi AI/Regulation.
- **Kịch bản B: Bài viết về bán dẫn đa thị trường (Multi-market semiconductor article)**
  Bài viết `d7a8acf9194bb4caf4eb3a9bca1634ea8ca006ed739d269d7ebc3c58baf8df4d` liên kết các thị trường EU và CN về các biện pháp kiểm soát xuất khẩu bán dẫn (semiconductor).
- **Kịch bản C: Bài viết không liên quan (Irrelevant article)**
  Bài viết `83f175303ba922d1ab1d909a3e482111e9c9652ba1dffc473f7493ad55a90e01` được phân loại là `is_relevant=false` và không sinh ra candidate nào.
- **Kịch bản D: Khuyết thiếu mô tả (Missing description)**
  Bài viết `7d276efcc07e5bb7014f7fb232eb95e5829f14f1d744e46888f0ad3495160897` có `description` là `null` để kiểm thử empty states.
- **Kịch bản E: Khuyết thiếu thời gian xuất bản (Missing published_at)**
  Bài viết `f7b273c46868915ff960ca8be8d8b2630e4abdf81fc57f1e57fbab00c2156298` có thời gian `published_at` là `null`, sử dụng thời gian khám phá (discovery time) để thay thế khi tính toán độ mới (freshness).
- **Kịch bản F: Chủ đề bị ẩn ngăn chặn tạo ứng viên (Muted topic prevents candidate)**
  Bài viết `ce58c4cd17b348ffcc18a3f59659d34b6b910b756d26e75176537ffc441ee58b` được gắn thẻ AI và BANKING, nhưng `user-9-muted-topic` sẽ không nhận được vì họ đã ẩn (mute) topic AI.
- **Kịch bản G: Người dùng nhận cùng một chủ đề từ nhiều thị trường (User receives same topic from multiple markets)**
  Người dùng `user-5-energy` theo dõi Energy trên nhiều thị trường và sẽ khớp với cả bài viết `6f73e398f6b392c982d27c0a3fd7f01360f32bffdcb5ce721483db2c25796ea1` (EU) và các bài viết ở thị trường khác.
- **Kịch bản H: Ứng viên thông thường không khẩn cấp (Non-breaking NORMAL candidate)**
  Bài viết `5339aa1e74a634a00037a839db08ceff74ff235554136ddf6beafdb1b1a4d866` có độ tin cậy và số chiều khớp thấp, dẫn đến một candidate `NORMAL` không khẩn cấp (non-breaking).
- **Kịch bản I: Thị trường nguồn tin khác với thị trường nội dung (Source market differs from content markets)**
  Bài viết `dfb9b1f9f5a2f12db04a7a0e45fe0ba45e85773a177238579c2a6ed1b3448bfb` bắt nguồn từ nguồn US (`CanonicalArticle.market="US"`) nhưng thảo luận về lãi suất (interest rates) của EU (`ClassifiedArticle.markets=["EU"]`).

Lưu ý: `description` synthetic tồn tại để phục vụ phát triển UI; quyền hiển thị trên production vẫn phụ thuộc vào cấu hình source. Không để lộ `service_role` hoặc tự động cho rằng mọi `articles.description` đều có thể được hiển thị công khai (publicly displayed).

## Ranh giới quyền sở hữu và mức độ sẵn sàng (Ownership and readiness boundary)

Tầng lưu trữ (persistence) của `UserPreference` thuộc về Product/SWE. Tệp JSON preference này chỉ minh họa cho hợp đồng dùng chung (shared contract) được DE tiêu thụ. Nó không chứng minh cũng không tạo ra một bảng dữ liệu ưu tiên trên môi trường production và không được coi là cơ sở để DE tự định nghĩa cấu trúc (schema) riêng.

Việc thiếu luồng đối chiếu tự động (production matching runner) không cản trở SWE Data Ready v1 sử dụng gói dữ liệu này. Nó chỉ cản trở việc tạo ra dữ liệu `alert_candidates` thực. Việc đưa dữ liệu thực vào cũng yêu cầu phải có nguồn preference do Product/SWE sở hữu, một trình đọc (read adapter) cụ thể, và hai tệp lệnh di chuyển cơ sở dữ liệu (migrations) của DE-012 phải được áp dụng lên máy chủ (remote).

DE-013 vẫn đang `PAUSED`.

## Cấu trúc Dữ liệu (Data Contracts)

Dưới đây là cấu trúc JSON (schema) tiêu chuẩn của từng loại dữ liệu theo thiết kế hệ thống, giúp SWE dễ dàng đối chiếu khi phát triển giao diện:

### 1. CanonicalArticle (Dữ liệu bài viết gốc)
```json
{
  "article_id": "deterministic-or-stable-id",
  "source_id": "us_federal_register",
  "source_item_id": "optional-upstream-id",
  "url": "https://example.org/item/123?utm_source=feed",
  "canonical_url": "https://example.org/item/123",
  "title": "Normalized title",
  "description": "Normalized source description/snippet",
  "language": "en",
  "market": "US",
  "published_at": "2026-08-14T14:00:00Z",
  "discovered_at": "2026-08-14T14:05:00Z",
  "content_hash": "sha256-or-other-approved-hash"
}
```
**Giải thích thuộc tính:**
- `article_id`: Định danh duy nhất (dạng hash) của bài viết, đóng vai trò khóa chính (Primary key).
- `source_id`: Mã hệ thống của nguồn tin (ví dụ: `us_federal_register`).
- `source_item_id`: ID định danh gốc từ hệ thống của nhà xuất bản (có thể `null`).
- `url`: Đường dẫn URL thô (raw) trỏ tới bài báo.
- `canonical_url`: Đường dẫn đã được chuẩn hóa để loại bỏ tham số theo dõi (phục vụ chống trùng lặp).
- `title`: Tiêu đề bài viết đã chuẩn hóa Unicode.
- `description`: Mô tả ngắn hoặc đoạn trích dẫn (có thể `null`).
- `language`: Mã ngôn ngữ của bài viết (ví dụ: `en`, `vi`).
- `market`: Mã thị trường của nguồn phát hành (ví dụ: `US`, `VN`).
- `published_at`: Thời gian xuất bản được ghi nhận từ nguồn tin. Nếu không xác định được, trường này là `null` (không bao giờ lấy giờ crawl đắp vào).
- `discovered_at`: Thời điểm hệ thống lần đầu tiên phát hiện/crawl được bài viết (chuẩn UTC).
- `content_hash`: Mã băm của tiêu đề và mô tả, dùng để phát hiện trùng lặp xuyên suốt hệ thống.

### 2. ClassifiedArticle (Dữ liệu đã phân loại)
```json
{
  "article_id": "article-id",
  "classifier_version": "classification-v2",
  "is_relevant": true,
  "markets": ["US", "EU"],
  "category": "LAW_POLICY",
  "topics": ["AI", "REGULATION"],
  "confidence": 0.93,
  "classified_at": "2026-08-14T14:06:00Z"
}
```
**Giải thích thuộc tính:**
- `article_id`: Khóa ngoại tham chiếu về ID của `CanonicalArticle`.
- `classifier_version`: Đánh dấu hệ thống phân loại (`classification-v1` là DeepSeek-first, `classification-v2` là hybrid).
- `is_relevant`: Biến boolean cho biết bài báo có liên quan đến các danh mục kinh doanh không. Nếu `false`, thì `markets`, `category` và `topics` luôn rỗng/null.
- `markets`: Mảng (tối đa 4) chứa các thị trường được nhắc tới trong nội dung (`VN`, `US`, `EU`, `CN`).
- `category`: Một phân mục ngành lớn thống nhất (`LAW_POLICY`, `ENERGY`, `TECHNOLOGY`, `REAL_ESTATE`, `FINANCE`).
- `topics`: Mảng (tối đa 5) chủ đề chuyên sâu được kiểm soát (ví dụ: `AI`, `BANKING`, `REGULATION`...).
- `confidence`: Độ tin cậy báo cáo từ công cụ phân loại (từ 0.0 đến 1.0).
- `classified_at`: Timestamp (chuẩn UTC) thời điểm quy trình gán nhãn hoàn tất.

### 3. UserPreference (Cài đặt người dùng)
```json
{
  "user_id": "user-id",
  "markets": ["VN", "US"],
  "categories": ["TECHNOLOGY", "FINANCE"],
  "topics": ["AI", "BANKING"],
  "muted_source_ids": [],
  "muted_topics": [],
  "breaking_alert_enabled": true,
  "hourly_update_enabled": true,
  "daily_digest_enabled": true
}
```
**Giải thích thuộc tính:**
- `user_id`: Định danh người dùng.
- `markets`: Danh sách thị trường người dùng muốn theo dõi.
- `categories`: Danh sách phân mục người dùng muốn theo dõi.
- `topics`: Danh sách chuyên đề người dùng muốn nhận tin.
- `muted_source_ids`: Mảng các nguồn tin cụ thể bị người dùng chặn (mute).
- `muted_topics`: Mảng các chuyên đề bị người dùng chặn (mute có độ ưu tiên cao nhất khi đối chiếu).
- `breaking_alert_enabled`: Bật/tắt cờ cho các tin nóng cần thông báo tức thời (boolean).
- `hourly_update_enabled`: Bật/tắt cờ tổng hợp tin gửi mỗi giờ (boolean).
- `daily_digest_enabled`: Bật/tắt cờ tổng hợp bản tin cuối ngày (boolean).

### 4. AlertCandidate (Ứng viên cảnh báo)
```json
{
  "candidate_id": "stable-id",
  "user_id": "user-id",
  "article_id": "article-id",
  "matched_at": "2026-08-14T14:07:00Z",
  "match_reasons": [
    "market:US",
    "category:TECHNOLOGY",
    "topic:AI"
  ],
  "importance": "NORMAL",
  "relevance_score": 0.82,
  "breaking_eligible": false
}
```
**Giải thích thuộc tính:**
- `candidate_id`: ID định danh của cảnh báo (tính toán dựa trên user_id và article_id).
- `user_id`: Định danh người nhận.
- `article_id`: Định danh bài viết cần thông báo.
- `matched_at`: Thời gian bài viết và sở thích của user khớp nhau.
- `match_reasons`: Danh sách các nguyên nhân khớp tín hiệu (ví dụ: bài báo thuộc `market:US` và `topic:AI` mà user có theo dõi). SWE có thể dùng mảng này để build UI giải thích "Tại sao bạn thấy tin này".
- `importance`: Ngưỡng quan trọng (`NORMAL` hoặc `HIGH`).
- `relevance_score`: Điểm số liên quan `[0.0, 1.0]` hỗ trợ để sắp xếp thứ tự hiển thị ưu tiên trên Feed UI.
- `breaking_eligible`: Đủ điều kiện kỹ thuật để kích hoạt push notification khẩn cấp (boolean). Tùy thuộc vào SWE quyết định luồng đẩy (push).
