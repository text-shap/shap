import numpy as np
import scipy as sp
from ._model import Model
from ..utils import safe_isinstance, record_import_error
from .. import models


class TeacherForcingLogits(Model):
    def __init__(
        self,
        model,
        tokenizer=None,
        generation_function_for_target_sentence_ids=None,
        similarity_model=None,
        similarity_tokenizer=None,
        device=None,
    ):
        """Generates scores (log odds) for output text explanation algorithms.

        This class supports generation of log odds for transformer models as well as functions. It also provides
        functionality to score custom output text by passing the generation_function_for_target_sentence_ids. In model agnostic
        cases (model is function) it expects a similarity_model and similarity_tokenizer to approximate log odd scores
        for target sentence generated by the model.

        Parameters
        ----------
        model: object or function
            A object of any pretrained transformer model or function which is to be explained.

        tokenizer: object
            A tokenizer object(PreTrainedTokenizer/PreTrainedTokenizerFast) which is used to tokenize source and target sentence.

        generation_function_for_target_sentence_ids: function
            A function which is used to generate custom target ids. Log odds will be generated for these custom target ids.

        similarity_model: object
            A pretrained transformer model object which is used in model agnostic scenario to approximate log odds.

        similarity_tokenizer: object
            A tokenizer object(PreTrainedTokenizer/PreTrainedTokenizerFast) which is used to tokenize sentence in model agnostic scenario.

        device: "cpu" or "cuda" or None
            By default, it infers if system has a gpu and accordingly sets device. Should be 'cpu' or 'gpu'.

        Returns
        -------
        numpy.array
            The scores (log odds) of generating target sentence ids using the model.
        """
        super(TeacherForcingLogits, self).__init__(model)

        # self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu') if device is None else device
        self.tokenizer = tokenizer
        self.device = device
        # assign text generation function
        if safe_isinstance(model, "transformers.PreTrainedModel") or safe_isinstance(
            model, "transformers.TFPreTrainedModel"
        ):
            if generation_function_for_target_sentence_ids is None:
                if safe_isinstance(model, "transformers.PreTrainedModel"):
                    self.generation_function_for_target_sentence_ids = (
                        models.PTTextGeneration(
                            self.model, tokenizer=self.tokenizer, device=self.device
                        )
                    )
                elif safe_isinstance(model, "transformers.TFPreTrainedModel"):
                    self.generation_function_for_target_sentence_ids = (
                        models.TFTextGeneration(
                            self.model, tokenizer=self.tokenizer, device=self.device
                        )
                    )
                else:
                    raise Exception(
                        "Cannot determine generation_function_for_target_sentence_ids to be assigned in TeacherForcingLogits. Please define model of instance transformers.PreTrainedModel or transformers.TFPreTrainedModel."
                    )
            else:
                self.generation_function_for_target_sentence_ids = (
                    generation_function_for_target_sentence_ids
                )
            self.model_agnostic = False
            # self.model = self.to_device(model)
            self.similarity_model = model
            self.similarity_tokenizer = tokenizer
        else:
            if generation_function_for_target_sentence_ids is None:
                if safe_isinstance(similarity_model, "transformers.PreTrainedModel"):
                    self.generation_function_for_target_sentence_ids = (
                        models.PTTextGeneration(
                            self.model,
                            similarity_tokenizer=similarity_tokenizer,
                            device=self.device,
                        )
                    )
                elif safe_isinstance(
                    similarity_model, "transformers.TFPreTrainedModel"
                ):
                    self.generation_function_for_target_sentence_ids = (
                        models.TFTextGeneration(
                            self.model,
                            similarity_tokenizer=similarity_tokenizer,
                            device=self.device,
                        )
                    )
                else:
                    raise Exception(
                        "Cannot determine generation_function_for_target_sentence_ids to be assigned in TeacherForcingLogits. Please define similarity_model of instance transformers.PreTrainedModel or transformers.TFPreTrainedModel."
                    )
            else:
                self.generation_function_for_target_sentence_ids = (
                    generation_function_for_target_sentence_ids
                )
            # self.similarity_model = self.to_device(similarity_model)
            self.similarity_model = similarity_model
            self.similarity_tokenizer = similarity_tokenizer
            self.model_agnostic = True
        # initializing X which is the original input for every new row of explanation
        self.X = None
        self.target_sentence_ids = None
        self.output_names = None

        if self.__class__ is TeacherForcingLogits:
            # assign the right subclass
            if safe_isinstance(self.similarity_model, "transformers.PreTrainedModel"):
                self.__class__ = models.PTTeacherForcingLogits
                models.PTTeacherForcingLogits.__init__(
                    self,
                    self.model,
                    self.tokenizer,
                    self.generation_function_for_target_sentence_ids,
                    self.similarity_model,
                    self.similarity_tokenizer,
                    self.device,
                )
            elif safe_isinstance(
                self.similarity_model, "transformers.TFPreTrainedModel"
            ):
                self.__class__ = models.TFTeacherForcingLogits
                models.TFTeacherForcingLogits.__init__(
                    self,
                    self.model,
                    self.tokenizer,
                    self.generation_function_for_target_sentence_ids,
                    self.similarity_model,
                    self.similarity_tokenizer,
                    self.device,
                )
            else:
                raise Exception(
                    "Cannot determine subclass to be assigned in TeacherForcingLogits. Please define similarity model or model of instance transformers.PreTrainedModel or transformers.TFPreTrainedModel."
                )

    def __call__(self, masked_X, X):
        """Computes log odds scores from a given batch of masked input and original input for text/image.

        Parameters
        ----------
        masked_X: numpy.array
            An array containing a list of masked inputs.

        X: numpy.array
            An array containing a list of original inputs

        Returns
        -------
        numpy.array
            A numpy array of log odds scores for every input pair (masked_X, X)
        """
        output_batch = []
        for masked_x, x in zip(masked_X, X):
            # update target sentence ids and original input for a new explanation row
            self.update_cache_X(x)
            # pass the masked input from which to generate source sentence ids
            source_sentence_ids = self.get_source_sentence_ids(masked_x)
            logits = self.get_teacher_forced_logits(
                source_sentence_ids, self.target_sentence_ids
            )
            logodds = self.get_logodds(logits)
            output_batch.append(logodds)
        return np.array(output_batch)

    def update_cache_X(self, X):
        """The function updates original input(X) and target sentence ids.

        It mimics the caching mechanism to update the original input and target sentence ids
        that are to be explained and which updates for every new row of explanation.

        Parameters
        ----------
        X: string or numpy.array
            Input(Text/Image) for an explanation row.
        """
        # check if the source sentence has been updated (occurs when explaining a new row)
        if (
            (self.X is None)
            or (isinstance(self.X, np.ndarray) and (self.X != X).all())
            or (isinstance(self.X, str) and (self.X != X))
        ):
            self.X = X
            self.output_names = self.get_output_names_and_update_target_sentence_ids(
                self.X
            )

    def get_output_names_and_update_target_sentence_ids(self, X):
        """Gets the output tokens from input(X) by computing the
            target sentence ids using the using the generation_function_for_target_sentence_ids()
            and next getting output names using the similarity_tokenizer.

        Parameters
        ----------
        X: string or numpy array
            Input(Text/Image) for an explanation row.
        Returns
        -------
        list
            A list of output tokens.
        """
        self.target_sentence_ids = self.generation_function_for_target_sentence_ids(X)
        return self.similarity_tokenizer.convert_ids_to_tokens(
            self.target_sentence_ids[0, :]
        )

    def get_source_sentence_ids(self, X):
        """Implement in subclass. Returns a tensor of sentence ids."""
        pass

    def get_logodds(self, logits):
        """Implement in subclass. Returns a np.array of logodds."""
        pass

    def get_teacher_forced_logits(self, source_sentence_ids, target_sentence_ids):
        """Implement in subclass. Returns a np.array of logits."""
        pass