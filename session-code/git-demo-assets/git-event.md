(github.event_name == 'pull_request' &&
 github.event.pull_request.head.repo.full_name == github.repository) ||
(github.event_name == 'issue_comment' &&
 github.event.issue.pull_request &&
 contains(github.event.comment.body, '@kiro') &&
 contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)) ||
(github.event_name == 'pull_request_review_comment' &&
 contains(github.event.comment.body, '@kiro') &&
 contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
