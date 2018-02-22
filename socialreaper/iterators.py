from urllib.parse import parse_qs, urlparse

from .apis import Facebook as FacebookApi, Twitter as TwitterApi, Reddit as RedditApi, Youtube as YoutubeApi
from .builders import FacebookFunctions
from .exceptions import ApiError
from .tools import flatten


class IterError(Exception):
    def __init__(self, e, variables):
        self.error = e
        self.vars = variables

    def __str__(self):
        return "An API error has occurred:  " + str(self.error)


class Iter:
    def __init__(self):
        # API object
        self.api = None

        # Response from api
        self.response = {}

        # Data from the response
        self.data = []

        # Index of data
        self.i = 0

        # Total data downloaded
        self.total = 0

        # Max data to gather, 0 for unlimited
        self.max = 0

        # Paging count, for restarting progress
        self.page_count = 0

        # The set of all headings used in the dataset
        self.headings = set()

    def __iter__(self):
        return self

    def __next__(self):
        # If not at the end of data, return the next element, else get more
        if self.i < len(self.data):
            result = self.data[self.i]
            self.i += 1
            self.total += 1

            # Return next data if max is less than or equal to total
            if self.max and self.total > self.max:
                raise StopIteration
            else:
                return result

        else:
            try:
                self.get_data()
                for item in self.data:
                    self.headings.update(item.keys())

            except StopIteration:
                raise StopIteration
            self.i = 0
            return self.__next__()

    def page_jump(self, count):
        """
        Page through data quickly. Used to resume failed job or jump to another
        page
        :param count: The number of pages to iterate over
        """
        for i in range(count):
            self.get_data()

    def get_data(self):
        """
        Obtain the data to iterate over from the API
        :return:
        """
        pass

    def get_headings(self):
        return self.headings


class Source:
    @staticmethod
    def merge(args, fields):
        if not args:
            args = {}

        if not fields:
            return args

        args['fields'] = ",".join(fields)
        return args

    @staticmethod
    def none_to_dict(value):
        return {} if not value else value


def merge(args, fields):
    if not args:
        args = {}

    if not fields:
        return args

    args['fields'] = fields
    return args


class IterIter:
    def __init__(self, outer, key, inner_func, inner_args):
        # Outer iter to obtain keys from
        self.outer = outer

        # Key string for outer function's data
        self.key = key

        # Key used on inner functions
        self.inner_key = None

        # Inner iter to obtain data from
        self.inner = None

        # The function to create the inner iter from
        self.inner_func = inner_func

        # The inner function's arguments
        self.inner_args = inner_args

        self.include_parents = False
        if inner_args.get('include_parents'):
            self.include_parents = bool(inner_args.pop('include_parents'))

        # Does the outer iter need a step
        self.outer_jump = True

    def __iter__(self):
        return self

    def __next__(self):
        # If outer iter needs to step
        if self.outer_jump:
            # Get key from outer iter's return
            # When outer iter is over, StopIteration is raised
            self.inner_key = flatten(self.outer.__next__()).get(self.key)
            # Create the inner iter by calling the function with key and args
            self.inner = self.inner_func(self.inner_key, **self.inner_args)
            # Toggle jumping off
            self.outer_jump = False

        # Return data from inner iter
        try:
            next_item = self.inner.__next__()
            if self.include_parents:
                next_item['parent_id'] = self.inner_key
            return next_item
            # return self.inner.__next__()
        except StopIteration:
            # If inner iter is over, step outer
            self.outer_jump = True
            return self.__next__()


class Facebook(Source, FacebookFunctions):
    def __init__(self, access_token):
        super().__init__()
        self.api_key = access_token
        self.dummy_api = FacebookApi(access_token)

        # Make use of nested queries, limiting scraping time
        self.nested_queries = False

    def test(self):
        try:
            api = FacebookApi(self.api_key)
            api.api_call('facebook', {'access_token': self.api_key})
            return True, "Working"

        except ApiError as e:
            return False, e

    def iter_iter(self, *args, **kwargs):
        return IterIter(*args, kwargs)

    def no_edge(self, node, fields, **kwargs):
        return iter([])
        # return self.FacebookIter(self.api_key, node, "", fields, **kwargs)

    def one_edge(self, node, edge, fields, **kwargs):
        return self.SingleIter(self.api_key, node, edge, fields, **kwargs)

    def two_edge(self, node, outer_func, inner_func, first_fields,
                 second_fields, first_args, second_args):

        first_args = merge(first_args, first_fields)
        second_args = merge(second_args, second_fields)
        return IterIter(outer_func(node, **first_args), "id",
                        inner_func,
                        second_args)

    def three_edge(self, node, outer_func, inner_func, first_fields,
                   second_fields, third_fields, first_args, second_args,
                   third_args):

        first_args = merge(first_args, first_fields)
        second_args = merge(second_args, second_fields)
        third_args = merge(third_args, third_fields)
        return IterIter(
            outer_func(node, None, None, first_args,
                       second_args), "id", inner_func, third_args)

    class FacebookIter(Iter):
        def __init__(self, api_key, node, edge, fields=None,
                     reverse_order=False, **kwargs):
            super().__init__()
            self.api = FacebookApi(api_key)

            self.node = node
            self.edge = edge
            self.fields = fields
            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))
            self.params = kwargs

            # Reverse paging order if in reverse mode
            self.next = 'previous' if reverse_order else 'next'
            self.after = 'before' if reverse_order else 'after'

        def get_data(self):
            self.page_count += 1

            try:
                self.response = self.api.node_edge(
                    self.node, self.edge, fields=self.fields,
                    params=self.params)
                self.data = self.response['data']

                paging = self.response.get('paging')

                if not paging:
                    raise StopIteration

                if paging.get('next'):
                    # Parse the next url and extract the params
                    self.params = parse_qs(urlparse(paging[self.next])[4])
                else:
                    if paging.get('cursors'):
                        # Replace the after parameter
                        self.params[self.after] = paging['cursors'][self.after]
                    else:
                        raise StopIteration

            except ApiError as e:
                raise IterError(e, vars(self))

    class SingleIter(Iter):
        def __init__(self, api_key, node, fields=None,
                     reverse_order=False, **kwargs):
            super().__init__()

            self.api = FacebookApi(api_key)

            self.node = node
            self.fields = fields
            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))
            self.params = kwargs

        def get_data(self):
            if self.response:
                raise StopIteration
            try:
                self.response = self.api.node_edge(
                    self.node, "", fields=self.fields,
                    params=self.params)

                self.data = [self.response]
            except ApiError as e:
                raise IterError(e, vars(self))


class Twitter(Source):
    def __init__(self, api_key, api_secret, access_token, access_token_secret):
        super().__init__()

        self.app_key = api_key
        self.app_secret = api_secret
        self.oauth_token = access_token
        self.oauth_token_secret = access_token_secret

        self.dummy_api = TwitterApi(api_key, api_secret, access_token, access_token_secret)

    class TwitterIter(Iter):
        def __init__(self, function, query, **kwargs):
            super().__init__()
            self.function = function
            self.query = query

            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))

            self.params = kwargs

        def _get_max_id(self):
            pass

        def _read_response(self):
            pass

        def get_data(self):
            self.page_count += 1

            self._get_max_id()

            try:
                self.response = self.function(self.query, **self.params)
                self.data = self._read_response()
            except ApiError as e:
                raise IterError(e, vars(self))

    class SearchIter(TwitterIter):
        def __init__(self, function, query, **kwargs):
            super().__init__(function, query, **kwargs)

        def _get_max_id(self):
            metadata = self.response.get('search_metadata')
            if metadata:
                next_results = metadata.get('next_results')
                if next_results:
                    self.params['max_id'] = parse_qs(next_results[1:]).get('max_id')[0]
                else:
                    raise StopIteration

        def _read_response(self):
            return self.response.get('statuses')

    class UserIter(TwitterIter):
        def __init__(self, function, query, **kwargs):
            super().__init__(function, query, **kwargs)

        def _get_max_id(self):
            if len(self.response) > 0:
                self.params['max_id'] = self.response[-1]['id'] - 1
            elif self.page_count > 1:
                raise StopIteration

        def _read_response(self):
            return self.response

    def search(self, query, **kwargs):
        return self.SearchIter(self.dummy_api.search, query, **kwargs)

    def user(self, query, **kwargs):
        return self.UserIter(self.dummy_api.user, query, **kwargs)


class Reddit(Source):
    def __init__(self, application_id, application_secret):
        super().__init__()

        self.application_id = application_id
        self.application_secret = application_secret

        self.dummy_api = RedditApi(application_id, application_secret)

    class RedditIter(Iter):
        def __init__(self, function, **kwargs):
            super().__init__()

            self.function = function

            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))

            self.params = kwargs

        def _read_response(self):
            pass

        def _get_after(self):
            pass

        def get_data(self):
            self.page_count += 1

            self._get_after()

            try:
                self.response = self.function(**self.params)
                self.data = self._read_response()
            except ApiError as e:
                raise IterError(e, vars(self))

    class SearchIter(RedditIter):
        def __init__(self, function, query, **kwargs):
            super().__init__(function, **kwargs)

            self.params['query'] = query

        def _get_after(self):
            data = self.response.get('data')
            if data:
                after = data.get('after')
                if after:
                    self.params['page'] = after
                else:
                    raise StopIteration

        def _read_response(self):
            return self.response['data']['children']

    class SubredditIter(RedditIter):
        def __init__(self, function, subreddit, **kwargs):
            super().__init__(function, **kwargs)

            self.params['subreddit'] = subreddit

        def _get_after(self):
            data = self.response.get('data')
            if data:
                after = data.get('after')
                if after:
                    self.params['page'] = after
                else:
                    raise StopIteration

        def _read_response(self):
            return self.response['data']['children']

    class UserIter(RedditIter):
        def __init__(self, function, user, **kwargs):
            super().__init__(function, **kwargs)

            self.params['user'] = user

        def _get_after(self):
            data = self.response.get('data')
            if data:
                after = data.get('after')
                if after:
                    self.params['page'] = after
                else:
                    raise StopIteration

        def _read_response(self):
            return self.response['data']['children']

    class ThreadIter(RedditIter):
        def __init__(self, function, thread, subreddit, **kwargs):
            super().__init__(function, **kwargs)

            self.params['subreddit'] = subreddit
            self.params['thread'] = thread

        def _get_after(self):
            try:
                data = self.response[0].get('data')
                if data:
                    after = data.get('after')
                    if after:
                        self.params['page'] = after
                    else:
                        raise StopIteration
            except KeyError:
                pass

        def _read_response(self):
            return self.response[0]['data']['children']

    class ThreadCommentsIter(Iter):
        def __init__(self, api, subreddit, thread, **kwargs):
            super().__init__()

            self.api = api
            self.subreddit = subreddit
            self.thread = thread
            self.params = kwargs

            self.level = 0
            self.reply_data = []
            self.more = []
            self.more_i = 0
            self.chunk_size = 20

            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))

        def _extract_comment(self, comment):
            """
            Get the parent comment and replies from a comment

            :param comment: The parent comment
            :return: A list of comments, with the parent at the start
            """

            lst = []
            if comment['data'].get('replies'):
                for reply in comment['data']['replies']['data']['children']:
                    lst.append(reply)
                    comments = self._extract_comment(reply)
                    lst.extend(comments)

                del comment['data']['replies']

                # if include_comment:
                #     lst.insert(0, comment)
            return lst

        def _classify_comment(self, comments, include_top=False):
            data = []

            if include_top:
                data.extend(comments)

            for comment in comments:

                replies = self._extract_comment(comment)

                for reply in replies:
                    if reply['data']['id'] == "dueic24":
                        print()
                    if reply['kind'] == 'more':
                        self.more.extend(reply['data']['children'])
                    else:
                        data.append(reply)

            return data

        def get_data(self):
            self.page_count += 1

            if self.level == 0:
                try:
                    self.response = self.api.thread_comments(self.thread, self.subreddit, **self.params)
                    self.data = self.response[1]['data']['children']
                except ApiError as e:
                    raise IterError(e, vars(self))
                self.level = 1
                return

            elif self.level == 1:
                replies = self._classify_comment(self.data)

                if len(replies) > 0:
                    self.level = 2
                    self.data = replies
                    return

            elif self.level == 2:
                if self.more_i < len(self.more):
                    chunk = self.more[self.more_i:self.more_i + self.chunk_size]
                    self.more_i += self.chunk_size

                    self.response = self.api.more_children(chunk, "t3_" + self.thread)
                    more_data = self.response['json']['data']['things']

                    self.data = self._classify_comment(more_data, include_top=True)
                    return

            raise StopIteration

    def search(self, query, **kwargs):
        return self.SearchIter(self.dummy_api.search, query, **kwargs)

    def search_user(self, query, **kwargs):
        return IterIter(self.search(query), 'data.author', self.user, kwargs)

    def search_thread_comments(self, query, **kwargs):
        return IterIter(self.search(query), 'data.id', self.thread_comments, kwargs)

    def subreddit(self, subreddit, **kwargs):
        return self.SubredditIter(self.dummy_api.subreddit, subreddit, **kwargs)

    def subreddit_user(self, subreddit, **kwargs):
        return IterIter(self.subreddit(subreddit), 'data.author', self.user, kwargs)

    def subreddit_thread_comments(self, subreddit, **kwargs):
        return IterIter(self.subreddit(subreddit), 'data.id', self.thread_comments, kwargs)

    def user(self, user, **kwargs):
        return self.UserIter(self.dummy_api.user, user, **kwargs)

    def thread(self, thread, subreddit, **kwargs):
        return self.ThreadIter(self.dummy_api.thread_comments, thread, subreddit, **kwargs)

    def thread_comments(self, thread, subreddit, **kwargs):
        return self.ThreadCommentsIter(self.dummy_api, subreddit, thread, **kwargs)

    def thread_comments_user(self, subreddit, thread, **kwargs):
        return IterIter(self.thread_comments(subreddit, thread), 'data.author', self.user, kwargs)


class YouTube(Source):
    def __init__(self, api_key):
        super().__init__()

        self.api_key = api_key

        self.dummy_api = YoutubeApi(api_key)

    class YouTubeIter(Iter):
        def __init__(self, function, query, **kwargs):
            super().__init__()

            self.function = function

            if kwargs.get('count'):
                self.max = int(kwargs.pop('count'))

            self.params = kwargs
            self.query = query

        def _read_response(self):
            pass

        def _get_after(self):
            pass

        def get_data(self):
            self.page_count += 1

            self._get_after()

            try:
                self.response = self.function(self.query, **self.params)
                self.data = self._read_response()
            except ApiError as e:
                raise IterError(e, vars(self))

    class YouTubeSearchIter(YouTubeIter):
        def _read_response(self):
            return self.response['items']

        def _get_after(self):
            self.params['page'] = self.response.get('nextPageToken')

    class YoutubeVideoIter(YouTubeIter):
        def _read_response(self):
            return self.response['items']

        def _get_after(self):
            if self.response:
                raise StopIteration

    class YoutubeVideoCommentsIter(YouTubeIter):
        def _read_response(self):
            return self.response['items']

        def _get_after(self):
            self.params['page'] = self.response.get('nextPageToken')

    def search(self, query, **kwargs):
        return self.YouTubeSearchIter(self.dummy_api.search, query, **kwargs)

    def search_comments(self, query, **kwargs):
        return IterIter(self.search(query), 'id.videoId', self.video_comments, kwargs)

    def channel(self, channel, **kwargs):
        return self.YouTubeSearchIter(self.dummy_api.search, None, channel_id=channel, **kwargs)

    def channel_comments(self, channel, **kwargs):
        return IterIter(self.search(channel), 'id.videoId', self.video_comments, kwargs)

    def video(self, video, **kwargs):
        return self.YoutubeVideoIter(self.dummy_api.videos, video, **kwargs)

    def video_comments(self, video_id, **kwargs):
        return self.YoutubeVideoCommentsIter(self.dummy_api.video_comments, video_id, **kwargs)
