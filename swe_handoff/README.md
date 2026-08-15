# SWE Data Ready v1 — Gói Hợp đồng Dữ liệu Mô phỏng (Synthetic Contract Pack)

Thư mục này chứa các ví dụ synthetic (mô phỏng) được liên kết với nhau dành cho SWE phát triển giao diện đọc sau DE-012. Đây là tài liệu về contract (hợp đồng dữ liệu), không phải là bản dump cơ sở dữ liệu, seed cho migration, fixture cho production, và cũng không khẳng định rằng các dòng dữ liệu này tồn tại trên remote database.

## Danh sách Tệp (Files)

- `articles.sample.json`: 35 bản ghi `CanonicalArticle` dùng chung.
- `article_classifications.sample.json`: 35 bản ghi `ClassifiedArticle` dùng chung. File này cố ý loại trừ vòng đời persistence, claim token, retry, cost, và metadata của provider.
- `user_preferences.sample.json`: 12 bản ghi `UserPreference` synthetic dùng chung dưới key `records`, cùng một thông báo không phải môi trường production rõ ràng.
- `alert_candidates.sample.json`: các bản ghi `AlertCandidate` synthetic dùng chung được liên kết với các tổ hợp article/preference hợp lệ (positive). Bài viết không liên quan (irrelevant) cố ý không có candidate nào.

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
